from qemu import (
    MOp_AddIRQHub,
    MOp_AddIRQLine,
    QOMPropertyTypeLink,
    QOMPropertyTypeString,
    QOMPropertyTypeBoolean,
    QOMPropertyTypeInteger,
    QOMPropertyValue,
    MOp_AddDevProp,
    MOp_SetDevProp,
    MOp_SetDevParentBus,
    MachineNode,
    q_event_dict,
    q_event_list
)
from argparse import (
    ArgumentTypeError,
    ArgumentParser,
    ArgumentError
)
from re import (
    compile
)
from collections import (
    defaultdict,
    deque
)
from multiprocessing import (
    Process
)
from os import (
    remove,
    system
)
from os.path import (
    isfile,
    split,
    join
)
from sys import (
    stderr,
    path as python_path
)
# Add ours pyelftools to PYTHON_PATH before importing of pyrsp to substitute
# system pyelftools imported by pyrsp
for mod in ("pyrsp", "pyelftools"):
    path = join(split(__file__)[0], mod)
    if path not in python_path:
        python_path.insert(0, path)

from pyrsp.targets import (
    AMD64
)
from pyrsp.elf import (
    ELF
)
from hashlib import (
    sha1
)
from common import (
    lazy,
    intervalmap,
    pythonize,
    mlget as _,
    notifier,
    sort_topologically,
    PyGenerator,
    execfile
)
from traceback import (
    print_exc
)
from pyrsp.utils import (
    switch_endian,
    decode_data
)
from debug import (
    Watcher,
    InMemoryELFFile,
    DWARFInfoCache,
    Value,
    Type,
    TYPE_CODE_PTR,
    Runtime
)
from itertools import (
    count
)
from inspect import (
    getmembers,
    ismethod
)
from graphviz import (
    Digraph
)
from widgets import (
    asksaveas,
    VarMenu,
    HotKey,
    HotKeyBinding,
    ThreadControl,
    GUIProjectHistoryTracker,
    GUIProject,
    MachineDescriptionSettingsWidget,
    GUITk
)
from six.moves.tkinter_messagebox import (
    showerror
)
from six.moves import (
    range
)
from socket import (
    socket,
    AF_INET,
    SOCK_STREAM
)


def checksum(stream, block_size):
    "Given a stream computes SHA1 hash by reading block_size per block."

    buf = stream.read(block_size)
    hasher = sha1()
    while len(buf) > 0:
        hasher.update(buf)
        buf = stream.read(block_size)


def elf_stream_checksum(stream, block_size = 65536):
    return checksum(stream, block_size)


def elf_checksum(file_name):
    with open(file_name, "rb") as s: # Stream
        return elf_stream_checksum(s)


class QELFCache(ELF):
    """
Extended version of ELF file cache.

Extra features:
- SHA1 based modification detection code, `mdc` field.
- serialization to Python script by `PyGeneratior`

    """

    # names of fields to serialize
    SAVED = (
        # QELFCache fields
        "mdc",
        # backing class fields
        "entry", "rel", "workarea", "symbols", "addresses", "file_map",
        "src_map", "addr_map", "routines", "vars",
    )

    def __init__(self, name, **kw):
        mdc = kw.get("mdc", None)
        file_mdc = elf_checksum(name)

        if mdc is None:
            # build the cache
            super(QELFCache, self).__init__(name)
            self.mdc = file_mdc
        else:
            # check the cache
            if file_mdc != mdc:
                raise ValueError("File SHA1 %r was changed. Expected: %r" % (
                    file_mdc, mdc
                ))

            absent = deque()

            for field in self.SAVED:
                try:
                    val = kw[field]
                except KeyError:
                    absent.append(val)
                setattr(self, field, val)

            if absent:
                raise TypeError("Some data are absent: " + ", ".join(absent))

    def __gen_code__(self, gen):
        gen.reset_gen(self)
        gen.gen_field("")
        gen.pprint(self.name)
        for field in self.SAVED:
            gen.gen_field(field + " = ")
            gen.pprint(getattr(self, field))
        gen.gen_end()

    def __dfs_children__(self):
        return [self.addr_map] + list(self.file_map.values())

    def __var_base__(self):
        return "qec"


re_qemu_system_x = compile(".*qemu-system-.+$")


class QArgumentParser(ArgumentParser):

    def error(self, *args, **kw):
        stderr.write("Error in argument string. Ensure that `--` is passed"
            " before QEMU and its arguments.\n"
        )
        super(QArgumentParser, self).error(*args, **kw)


class RQOMTree(object):
    """ QEmu object model tree descriptor at runtime
    """

    def __init__(self):
        self.name2type = {}
        self.addr2type = {}

        # Types are found randomly (i.e. not in parent-first order).
        self.unknown_parents = defaultdict(list)

    def account(self, impl, name = None, parent = None):
        "Add a type"
        if impl.type.code == TYPE_CODE_PTR:
            # Pointer `impl` is definetly a value on the stack. It cannot be
            # used as a global. Same time `TypeImpl` is on the heap. Hence, it
            # can. I.e. a dereferenced `Value` should be used.
            impl = impl.dereference()
        if not impl.is_global:
            impl = impl.to_global()

        info_addr = impl.address

        t = RQOMType(self, impl, name = name, parent = parent)

        name = t.name
        parent = t.parent

        self.addr2type[info_addr] = t
        self.name2type[name] = t

        unk_p = self.unknown_parents

        n2t = self.name2type
        if parent in n2t:
            n2t[parent].children.append(t)
        else:
            unk_p[parent].append(t)

        if name in unk_p:
            t.children.extend(unk_p.pop(name))

        return t

    def __getitem__(self, addr_or_name):
        if isinstance(addr_or_name, str):
            return self.name2type[addr_or_name]
        else:
            return self.addr2type[addr_or_name]


class RQOMType(object):
    """ QEmu object model type descriptor at runtime
    """

    def __init__(self, tree, impl, name = None, parent = None):
        """
        :param impl:
            global runtime `Value` which is pointer to the `TypeImpl`

        :param name:
            `str`ing read form impl

        :param parent:
            `str`ing too
        """
        self.tree = tree
        self.impl = impl
        if name is None:
            name = impl["name"].fetch_c_string()
        if parent is None:
            parent = impl["parent"].fetch_c_string()
            # Parent may be None
        self.name, self.parent = name, parent

        self.children = []

        # Instance pointer can be casted to different C types. Remember those
        # types.
        self._instance_casts = set()

        # "device"
        self.realize = None

    def instance_casts(self):
        """ A QOM instance can be casted to C types corresponding to its
        ancestors too.
        """
        ret = set(self._instance_casts)
        for a in self.iter_ancestors():
            for cast in a._instance_casts:
                ret.add(cast)
        return ret

    # TODO: there is too many boilerplate code for `TypeImpl` fields access.
    # Consider to rewrite it in a common way. `__getitem__` ?

    @lazy
    def instance_init(self):
        impl = self.impl

        addr = impl["instance_init"].fetch_pointer()
        if addr:
            return impl.dic.subprogram(addr)
        return None

    @lazy
    def class_init(self):
        impl = self.impl

        addr = impl["class_init"].fetch_pointer()
        if addr:
            return impl.dic.subprogram(addr)
        return None

    def __dfs_children__(self):
        return self.children

    def iter_ancestors(self):
        n2t = self.tree.name2type
        cur = self.parent

        while cur is not None:
            t = n2t[cur]
            yield t
            cur = t.parent

    def implements(self, name):
        if name == self.name:
            return True

        try:
            t = self.tree.name2type[name]
        except KeyError:
            # the type given is unknown, `self` cannot implement it
            return False

        for a in self.iter_ancestors():
            if a is t:
                return True
        return False


# Characters disalowed in node ID according to DOT language. That is not a full
# list though.
# https://www.graphviz.org/doc/info/lang.html
re_DOT_ID_disalowed = compile(r"[^a-zA-Z0-9_]")


def gv_node(label):
    return re_DOT_ID_disalowed.sub("_", label)


class QOMTreeGetter(Watcher):

    def __init__(self, dic, interrupt = True, verbose = False):
        """
        :param interrupt:
            Stop QEmu and exit `RemoteTarget.run`.
        """
        super(QOMTreeGetter, self).__init__(dic, verbose = verbose)

        self.tree = RQOMTree()
        self.interrupt = interrupt

    def on_type_register_internal(self):
        # 63f7b10bc552be8a2cd1da87e8b27f9a5a217b91
        # v2.12.0
        "object.c:139" # type_register_internal

        t = self.tree.account(self.rt["ti"])

        if self.verbose:
            print("%s -> %s" % (t.parent, t.name))

    def on_type_initialize(self):
        # now the type and its ancestors are initialized

        # 63f7b10bc552be8a2cd1da87e8b27f9a5a217b91
        #"object.c:333" # before `class_init` call

        # v2.12.0
        "object.c:344"

        rt = self.rt
        type_impl = rt["ti"]

        ti_addr = type_impl.fetch_pointer()
        a2t = self.tree.addr2type

        if ti_addr not in a2t:
            # There are interfaces providing variations of regular types. They
            # do not path throug type_register_internal because its TypeInfo is
            # created and used directly by type_initialize_interface.
            return

        t = a2t[ti_addr]

        if t.implements("device"):
            cls = type_impl["class"]

            dev_cls = cls.cast("DeviceClass *")
            realize_addr = dev_cls["realize"].fetch_pointer()

            if realize_addr:
                t.realize = rt.dic.subprogram(realize_addr)

    def on_main(self):
        # main, just after QOM module initialization

        # 43ac51e66b421216856c752f9382cb6de3cfccad (!)
        #"vl.c:2989"

        # v2.12.0
        "vl.c:3075"

        if self.interrupt:
            self.rt.target.interrupt()

    def to_file(self, dot_file_name):
        graph = Digraph(
            name = "QOM",
            graph_attr = dict(
                rankdir = "LR"
            ),
            node_attr = dict(
                shape = "polygon",
                fontname = "Momospace"
            ),
            edge_attr = dict(
                style = "filled"
            ),
        )

        for t in sort_topologically(
            v for v in self.tree.name2type.values() if v.parent is None
        ):
            n = gv_node(t.name)
            label = t.name + "\\n0x%x" % t.impl.address
            if t._instance_casts:
                label += "\\n*"
                for cast in t._instance_casts:
                    label += "\\n" + cast.name

            graph.node(n, label = label)
            if t.parent:
                graph.edge(gv_node(t.parent), n)

        with open(dot_file_name, "wb") as f:
            f.write(graph.source)


class RQObjectProperty(object):
    """ Runtime QOM object property
    """

    def __init__(self, obj, prop, name = None, type = None):
        """
        :param obj:
            is corresponding `QInstance`
        :param prop:
            is `Value` representing `ObjectProperty`
        """
        self.obj = obj
        self.prop = prop
        if name is None:
            name = prop["name"].fetch_c_string()
        if type is None:
            type = prop["type"].fetch_c_string()
        self.name = name
        self.type = type

    @lazy
    def as_qom(self):
        # XXX: note that returned values are not default
        t = self.type
        if t.startswith("int") or t.startswith("uint"):
            return QOMPropertyValue(QOMPropertyTypeInteger, self.name, 0)
        elif t.startswith("link<"):
            return QOMPropertyValue(QOMPropertyTypeLink, self.name, None)
        elif t == "bool":
            return QOMPropertyValue(QOMPropertyTypeBoolean, self.name, False)
        else:
            return QOMPropertyValue(QOMPropertyTypeString, self.name, "")


class QInstance(object):
    """ Descriptor for QOM object at runtime.
    """

    def __init__(self, obj, type):
        """
        :param obj:
            Global runtime `Value`.

        :param type:
            instance of `RQOMType`
        """
        self.obj = obj
        self.type = type
        self.related = []

        # object
        self.properties = {}

        # qemu:memory-region:
        # bus
        self.name = None

        # qemu:memory-region
        self.size = None

        # device: the bus this device is attached to
        # bus: the device controlling this bus
        self.parent = None

        # device: buses controlled by the device
        # bus: devices on the bus
        self.children = []

        # irq:
        # tuple (dev. `QInstance`, GPIO name, GPIO index)
        #     for split IRQ that `dst[0]` is `self`
        self.src = None
        self.dst = None

    def relate(self, qinst):
        self.related.append(qinst)
        qinst.related.append(self)

    def unrelate(self, qinst):
        self.related.remove(qinst)
        qinst.related.renove(self)

    def account_property(self, prop):
        """
        :param prop:
            is `Value` representing `ObjectProperty`
        """
        if prop.type.code == TYPE_CODE_PTR:
            prop = prop.dereference()
        if not prop.is_global:
            prop = prop.to_global()

        rqo_prop = RQObjectProperty(self, prop)
        self.properties[rqo_prop.name] = rqo_prop

        return rqo_prop


@notifier(
    "device_creating", # QInstance
    "device_created", # QInstance
    "bus_created", # QInstance
    "bus_attached", # QInstance bus, QInstance device
    "device_attached", # QInstance device, QInstance bus
    "property_added", # QInstance, RQObjectProperty
    "property_set", # QInstance, RQObjectProperty, Value
    "irq_connected", # QInstance
    "irq_split_created", # QInstance (IRQ)
)
class MachineWatcher(Watcher):
    """ Watches for QOM API calls to reconstruct machine model and monitor its
    state at runtime.
    """

    def __init__(self, dic, qom_tree, verbose = False, interrupt = True):
        """
        @param interrupt
            Interrupt QEmu process after machine is created.
        """
        super(MachineWatcher, self).__init__(dic, verbose = verbose)
        self.tree = qom_tree
        # addr -> QInstance mapping
        self.instances = {}
        self.machine = None
        self.interrupt = interrupt

    def account_instance(self, obj, type_impl = None):
        """
        :param obj:
            `Value` representing object structure
        """
        if obj.type.code == TYPE_CODE_PTR:
            obj = obj.dereference()
        if not obj.is_global:
            obj = obj.to_global()

        if type_impl is None:
            type_impl = obj.cast("Object")["class"]["type"]

        addr = type_impl.fetch_pointer()
        rqom_type = self.tree.addr2type[addr]

        i = QInstance(obj, rqom_type)
        self.instances[obj.address] = i
        return i

    # Breakpoint handlers

    def on_obj_init_start(self):
        # object_initialize_with_type, before `object_init_with_type`

        # 63f7b10bc552be8a2cd1da87e8b27f9a5a217b91
        #"object.c:376"

        # v2.12.0
        "object.c:384"

        machine = self.machine
        if machine is None:
            return

        rt = self.rt
        impl = rt["type"]
        t = self.tree[impl.fetch_pointer()]

        inst = self.account_instance(rt["obj"], impl)
        inst.relate(machine)

        if t.implements("device"):
            if self.verbose:
                print("Creating device " + inst.type.name)
            self.current_device = inst

            self.__notify_device_creating(inst)
        elif t.implements("qemu:memory-region"):
            # print("Creating memory")
            self.current_memory = inst

    def on_obj_init_end(self):
        # object_initialize_with_type, return

        # 63f7b10bc552be8a2cd1da87e8b27f9a5a217b91
        #"object.c:378"

        # v2.12.0
        "object.c:386"

        if self.machine is None:
            return

        rt = self.rt
        addr = rt["obj"].fetch_pointer()

        inst = self.instances[addr]

        if inst.type.implements("device"):
            self.__notify_device_created(inst)

    def on_board_init_start(self):
        # 43ac51e66b421216856c752f9382cb6de3cfccad (!)
        #"vl.c:4510" # main, before `machine_class->init(current_machine)`

        # v2.12.0
        "hw/core/machine.c:829" # machine_run_board_init

        rt = self.rt
        # 43ac51e66b421216856c752f9382cb6de3cfccad (!)
        #machine_obj = rt["current_machine"]
        # v2.12.0
        machine_obj = rt["machine"]
        self.machine = inst = self.account_instance(machine_obj)

        desc = inst.type.impl["class"].cast("MachineClass*")["desc"]

        if not self.verbose:
            return

        print("Machine creation started\nDescription: " +
            desc.fetch_c_string()
        )

    def on_mem_init_end(self):
        # return from memory_region_init

        # 0ab8ed18a6fe98bfc82705b0f041fbf2a8ca5b60
        #"memory.c:1009"

        # v2.12.0
        "memory.c:1153"

        if self.machine is None:
            return

        rt = self.rt
        m = self.current_memory

        if m.obj.address != rt["mr"].fetch_pointer():
            raise RuntimeError("Unexpected memory initialization sequence")

        m.name = rt["name"].fetch_c_string()
        m.size = rt["size"].fetch(8)

        if not self.verbose:
            return
        print("Memory: %s 0x%x" % (m.name or "[nameless]", m.size))

    def on_board_init_end(self):
        # 43ac51e66b421216856c752f9382cb6de3cfccad (!)
        #"vl.c:4511" # main, after `machine_class->init(current_machine)`

        # v2.12.0
        "hw/core/machine.c:830" # machine_run_board_init

        self.remove_breakpoints()
        if self.interrupt:
            self.rt.target.interrupt()

        if not self.verbose:
            return

        print("Machine creation ended: " +
            # explicit casting is not required here, it's just for testing
            self.machine.type.impl.cast("TypeImpl")["name"].fetch_c_string()
        )

    def on_obj_prop_add(self):
        # object_property_add, before insertion to prop. table; property found
        # Do NOT set this breakpoint on `return` because it will catch all
        # `return` statements in the function.

        # 63f7b10bc552be8a2cd1da87e8b27f9a5a217b91
        #"object.c:954"

        # v2.12.0
        "object.c:975"

        if self.machine is None:
            return

        rt = self.rt
        obj = rt["obj"]
        obj_addr = obj.fetch_pointer()

        try:
            inst = self.instances[obj_addr]
        except KeyError:
            print("Skipping property for unaccounted object 0x%x of type"
                  " %s" % (
                    obj_addr,
                    obj["class"]["type"]["name"].fetch_c_string()
                )
            )
            return

        prop = inst.account_property(rt["prop"])

        self.__notify_property_added(inst, prop)

        if not self.verbose:
            return

        print("Object 0x%x (%s) -> %s (%s)" % (
            prop.obj.obj.address,
            prop.obj.type.name,
            prop.name,
            prop.type
        ))

    def on_obj_prop_set(self):
        # object_property_set (prop. exists and has a setter)

        # 63f7b10bc552be8a2cd1da87e8b27f9a5a217b91
        #"object.c:1094"

        # v2.12.0
        "object.c:1122"

        if self.machine is None:
            return

        rt = self.rt
        obj_addr = rt["obj"].fetch_pointer()
        name = rt["name"].fetch_c_string()
        try:
            inst = self.instances[obj_addr]
        except:
            print("Skipping value of property '%s' for unaccounted object"
                " 0x%x" % (name, obj_addr)
            )
            return

        prop = inst.properties[name]

        self.__notify_property_set(inst, prop, None)

        if not self.verbose:
            return

        print("Object 0x%x (%s) -> %s (%s) = 0x%x (Visitor)" % (
            prop.obj.obj.address,
            prop.obj.type.name,
            prop.name,
            prop.type,
            rt["v"].fetch_pointer()
        ))

    def on_qbus_realize(self):
        # v2.12.0
        "hw/core/bus.c:101" # qbus_realize, parrent may be NULL

        rt = self.rt
        bus = rt["bus"]

        bus_inst = self.instances[bus.fetch_pointer()]

        name = bus["name"].fetch_c_string()

        bus_inst.name = name

        self.__notify_bus_created(bus_inst)

        dev_addr = rt["parent"].fetch_pointer()
        if dev_addr:
            device_inst = self.instances[dev_addr]
            bus_inst.parent = device_inst
            device_inst.children.append(bus_inst)

            bus_inst.relate(device_inst)

            self.__notify_bus_attached(bus_inst, device_inst)

        if not self.verbose:
            return

        if dev_addr:
            print("Device 0x%x (%s) |----- bus 0x%s %s (%s)" % (
                device_inst.obj.address,
                device_inst.type.name,
                bus_inst.obj.address,
                bus_inst.name,
                bus_inst.type.name
            ))
        else:
            print("   ~-- bus 0x%s %s (%s)" % (
                bus_inst.obj.address,
                bus_inst.name,
                bus_inst.type.name
            ))

    def on_bus_unparent(self):
        # v2.12.0
        "hw/core/bus.c:123" # bus_unparent, before actual unparanting

        # TODO: test me
        rt = self.rt
        bus = rt["bus"]
        bus_inst = self.instances[bus.fetch_pointer()]
        parent = bus["parent"]
        device_inst = self.instances[parent.fetch_pointer()]

        bus_inst.parent = None
        device_inst.children.remove(bus_inst)

        device_inst.unrelate(bus_inst)

        if not self.verbose:
            return

        print("Device 0x%x (%s) |-x x- bus 0x%s %s (%s)" % (
            device_inst.obj.address,
            device_inst.type.name,
            bus_inst.obj.address,
            bus_inst.name,
            bus_inst.type.name
        ))

    def on_bus_add_child(self):
        # bus_add_child

        # 67980031d234aa90524b83bb80bb5d1601d29076
        #"hw/core/qdev.c:101" # returning

        # v2.12.0
        "hw/core/qdev.c:73"

        rt = self.rt
        bus = rt["bus"]
        bus_inst = self.instances[bus.fetch_pointer()]
        device_inst = self.instances[rt["child"].fetch_pointer()]

        device_inst.parent = bus_inst
        bus_inst.children.append(device_inst)
        bus_inst.relate(device_inst)

        self.__notify_device_attached(device_inst, bus_inst)

        if not self.verbose:
            return

        print("Bus 0x%x %s (%s) |----- device 0x%x (%s)" % (
            bus_inst.obj.address,
            bus_inst.name,
            bus_inst.type.name,
            device_inst.obj.address,
            device_inst.type.name
        ))

    def on_bus_remove_child(self):
        # bus_remove_child, before actual unparanting

        # 67980031d234aa90524b83bb80bb5d1601d29076
        #"hw/core/qdev.c:70"

        # v2.12.0
        "hw/core/qdev.c:57"

        print("not implemented")

    def on_qdev_get_gpio_in_named(self):
        # qdev_get_gpio_in_named, return

        # 67980031d234aa90524b83bb80bb5d1601d29076
        #"core/qdev.c:473"

        # v2.12.0
        "core/qdev.c:456"

        instances = self.instances
        rt = self.rt

        irq_addr = rt.returned_value.fetch_pointer()
        dst_addr = rt["dev"].fetch_pointer()
        dst_name = rt["name"].fetch_c_string()
        dst_idx = rt["n"].fetch(4) # int

        irq = instances[irq_addr]
        dst = instances[dst_addr]

        irq.dst = (dst, dst_name, dst_idx)

        self.check_irq_connected(irq)

    def on_qdev_connect_gpio_out_named(self):
        # qdev_connect_gpio_out_named, after IRQ was assigned and before
        # property name `propname` freed.

        # 67980031d234aa90524b83bb80bb5d1601d29076
        #"core/qdev.c:496"

        # v2.12.0
        "core/qdev.c:479"

        rt = self.rt

        irq_addr = rt["pin"].fetch_pointer()

        if not irq_addr:
            return

        instances = self.instances

        src_addr = rt["dev"].fetch_pointer()
        src_name = rt["name"].fetch_c_string()
        src_idx = rt["n"].fetch(4) # int

        src = instances[src_addr]
        irq = instances[irq_addr]

        irq.src = (src, src_name, src_idx)

        self.check_irq_connected(irq)

    def check_irq_connected(self, irq):
        src = irq.src
        dst = irq.dst

        if src is None or dst is None:
            return

        self.__notify_irq_connected(irq)

    def on_qemu_irq_split(self):
        # returning from `qemu_irq_split`

        # 67980031d234aa90524b83bb80bb5d1601d29076
        #"core/irq.c:121"

        # v2.12.0
        "core/irq.c:122"

        rt = self.rt
        instances = self.instances

        split_irq_addr = rt.returned_value.fetch_pointer()

        split_irq = instances[split_irq_addr]

        self.__notify_irq_split_created(split_irq)

        irq1 = instances[rt["irq1"].fetch_pointer()]
        irq2 = instances[rt["irq2"].fetch_pointer()]

        split_irq.dst = (split_irq, None, 0) # yes, to itself
        irq1.src = (split_irq, None, 0)
        irq2.src = (split_irq, None, 1)

        self.check_irq_connected(irq1)
        self.check_irq_connected(irq2)


class PCMachineWatcher(MachineWatcher):

    def on_pc_piix_gsi(self):
        # v2.12.0
        "pc_piix.c:301"
        # "pc_piix.c:195" # wrong position, it is for array access testing only

        rt = self.rt
        instances = self.instances

        gsi = rt["pcms"]["gsi"]
        # gsi is array of qemu_irq
        gsi_state = rt["gsi_state"]
        i8259_irq = gsi_state["i8259_irq"]
        ioapic_irq = gsi_state["ioapic_irq"]

        for i in range(24): # GSI_NUM_PINS, IOAPIC_NUM_PINS
            gsi_addr = gsi[i].fetch_pointer()
            gsi_inst = instances[gsi_addr]

            self._MachineWatcher__notify_irq_split_created(gsi_inst)
            # yes, to itself, like a split irq
            gsi_inst.dst = (gsi_inst, None, 0)
            self.check_irq_connected(gsi_inst)

            ioapic_irq_addr = ioapic_irq[i].fetch_pointer()
            if ioapic_irq_addr != 0:
                ioapic_inst = instances[ioapic_irq_addr]

                ioapic_inst.src = (gsi_inst, None, i)
                self.check_irq_connected(ioapic_inst)

            if i < 16: # ISA_NUM_IRQS
                i8259_irq_addr = i8259_irq[i].fetch_pointer()
                if i8259_irq_addr != 0:
                    i8259_inst = instances[i8259_irq_addr]

                    i8259_inst.src = (gsi_inst, None, i)
                    self.check_irq_connected(i8259_inst)

    def on_piix4_pm_gsi(self):
        # v.2.12.0
        "piix4.c:578"

        rt = self.rt

        irq_addr = rt["sci_irq"].fetch_pointer()
        src_addr = rt["dev"].fetch_pointer()

        src = self.instances[src_addr]
        irq = self.instances[irq_addr]

        irq.src = (src, None, None)

        self.check_irq_connected(irq)


class CastCatcher(object):
    """ A breakpoint handler that inspects all subprogram pointers those do
    refer to a given QOM instance and remebers types of those pointers as
    possible casts for instances of that QOM type.
    """

    def __init__(self, instance, runtime = None):
        self.inst = instance
        self.rt = runtime

    def __call__(self):
        inst = self.inst
        obj = inst.obj

        addr = obj.address
        qom_type = inst.type

        rt = self.rt
        if rt is None:
            rt = obj.runtime
        if rt is None:
            raise ValueError("Cannot obtain runtime")

        for datum_name in rt.subprogram.data:
            datum = rt[datum_name]
            datum_type = datum.type
            if datum_type.code != TYPE_CODE_PTR:
                continue
            datum_addr = datum.fetch_pointer()
            if datum_addr != addr:
                continue
            qom_type._instance_casts.add(datum_type.target_type)


class MachineReverser(object):

    def __init__(self, watcher, machine, tracker):
        """
        :type watcher: MachineWatcher
        :type machine: MachineNode
        :type tracker: GUIProjectHistoryTracker
        """
        self.watcher = watcher
        self.machine = machine
        self.tracker = tracker
        self.proxy = tracker.get_machine_proxy(machine)

        # auto assign event handlers
        for name, ref in getmembers(type(self)):
            if name[:4] == "_on_" and ismethod(ref):
                watcher.watch(name[4:], getattr(self, name))

        self.__next_node_id = 1
        self.inst2id = {}
        self.irq_inst2hub_id = {}
        self.id2inst = []

    def __id(self):
        _id = self.__next_node_id
        self.__next_node_id += 1
        return _id

    def _on_runtime_set(self, rt):
        self.rt = rt
        self.target = rt.target

    def _on_device_creating(self, inst):
        _id = self.__id()
        self.inst2id[inst] = _id
        self.id2inst.append(inst)

        _type = inst.type
        if _type.implements("pci-device"):
            self.proxy.add_device("PCIExpressDeviceNode", _id,
                qom_type = _type.name
            )
        elif _type.implements("sys-bus-device"):
            self.proxy.add_device("SystemBusDeviceNode", _id,
                qom_type = _type.name
            )
        else:
            self.proxy.add_device("DeviceNode", _id,
                qom_type = _type.name
            )

        self.proxy.commit()

        target = self.target
        cc = CastCatcher(inst)

        ii = _type.instance_init
        if ii:
            for addr in ii.epilogues:
                target.add_br(target.get_hex_str(addr), cc)

        realize = _type.realize
        if realize:
            for addr in realize.epilogues:
                target.add_br(target.get_hex_str(addr), cc)

    def _on_bus_created(self, bus):
        _id = self.__id()
        self.inst2id[bus] = _id
        self.id2inst.append(bus)

        bus_type = bus.type
        if bus_type.implements("System"):
            bus_class = "SystemBusNode"
        elif bus_type.implements("PCI"):
            bus_class = "PCIExpressBusNode"
        elif bus_type.implements("ISA"):
            bus_class = "ISABusNode"
        elif bus_type.implements("IDE"):
            bus_class = "IDEBusNode"
        elif bus_type.implements("i2c-bus"):
            bus_class = "I2CBusNode"
        else:
            bus_class = "BusNode"

        self.proxy.add_bus(bus_class, _id)
        self.proxy.commit()

    def _on_bus_attached(self, bus, device):
        bus_id = self.inst2id[bus]
        device_id = self.inst2id[device]

        self.proxy.append_child_bus(device_id, bus_id)
        self.proxy.commit()

    def _on_device_attached(self, device, bus):
        bus_id = self.inst2id[bus]
        device_id = self.inst2id[device]

        self.proxy.stage(MOp_SetDevParentBus, self.machine.id2node[bus_id],
            device_id
        )
        self.proxy.commit()

    def _on_property_added(self, obj, _property):
        prop = _property.prop
        setter_addr = prop["set"].fetch_pointer()

        if not setter_addr:
            return

        inst2id = self.inst2id
        if obj not in inst2id:
            return

        if not obj.type.implements("device"):
            return

        self.proxy.stage(MOp_AddDevProp, _property.as_qom, inst2id[obj])
        self.proxy.commit()

    def _on_property_set(self, obj, _property, val):
        prop = _property.prop
        setter_addr = prop["set"].fetch_pointer()

        if not setter_addr:
            return

        inst2id = self.inst2id
        if obj not in inst2id:
            return

        if not obj.type.implements("device"):
            return

        qom_prop = _property.as_qom

        self.proxy.stage(MOp_SetDevProp, qom_prop.prop_type,
            qom_prop.prop_val, # XXX: recover from `val`
            qom_prop,
            inst2id[obj]
        )
        self.proxy.commit()

    def _on_irq_connected(self, irq):
        i2i = self.inst2id

        _id = self.__id()
        i2i[irq] = _id
        self.id2inst.append(irq)

        src = irq.src
        dst = irq.dst

        src_inst = src[0]
        dst_inst = dst[0]

        ii2hi = self.irq_inst2hub_id

        # A split IRQ (hub) instance presents in both mappings. But in
        # `irq_inst2hub_id` it points to IRQ hub id while in `inst2id` it
        # points to IRQ line id
        if src_inst in ii2hi:
            src_id = ii2hi[src_inst]
        else:
            src_id = i2i[src_inst]

        if dst_inst in ii2hi:
            dst_id = ii2hi[dst_inst]
        else:
            dst_id = i2i[dst_inst]

        self.proxy.stage(MOp_AddIRQLine,
            src_id, dst_id,
            src[2], dst[2], # indices
            src[1], dst[1],  # names
            _id
        )
        self.proxy.commit()

    def _on_irq_split_created(self, irq):
        _id = self.__id()
        self.irq_inst2hub_id[irq] = _id
        self.id2inst.append(irq)

        self.proxy.stage(MOp_AddIRQHub, _id)
        self.proxy.commit()


class QEmuWatcherGUI(GUITk):

    def __init__(self, pht, mach_desc, runtime):
        GUITk.__init__(self, wait_msec = 1)

        self.title(_("QEmu Watcher"))

        self.pht = pht
        self.rt = runtime

        self.rowconfigure(0, weight = 1)
        self.columnconfigure(0, weight = 1)

        mdsw = MachineDescriptionSettingsWidget(mach_desc, self)
        mdsw.grid(row = 0, column = 0, sticky = "NESW")
        mdsw.mw.mdw.var_physical_layout.set(False)
        self.mdsw = mdsw

        # magic with layouts
        pht.p.add_layout(mach_desc.name, mdsw.gen_layout()).widget = mdsw

        self.columnconfigure(1, weight = 0)
        self.tc = tc = ThreadControl(self)
        tc.grid(row = 0, column = 1, sticky = "NESW")
        tc.set_target(runtime.target)

        self.task_manager.enqueue(self.co_rsp_poller())

        self.hk = hk = HotKey(self)
        hk.add_bindings([
            HotKeyBinding(self._on_save,
                key_code = 39,
                description = _("Save machine"),
                symbol = "S"
            )
        ])

        menubar = VarMenu(self)
        self.config(menu = menubar)

        filemenu = VarMenu(menubar, tearoff = False)
        menubar.add_cascade(label = _("File"), menu = filemenu)

        filemenu.add_command(
            label = _("Save machine"),
            command = self._on_save,
            accelerator = hk.get_keycode_string(self._on_save)
        )

    def _on_save(self):
        fname = asksaveas(self,
            [(_("QDC GUI Project defining script"), ".py")],
            title = _("Save machine")
        )

        if not fname:
            return

        self.save_project_to_file(fname)

    def try_save_project_to_file(self, file_name):
        try:
            open(file_name, "wb").close()
        except IOError as e:
            if not e.errno == 13: # Do not remove read-only files
                try:
                    remove(file_name)
                except:
                    pass

            showerror(
                title = _("Cannot save project").get(),
                message = str(e)
            )
            return

        self.save_project_to_file(file_name)

    def save_project_to_file(self, file_name):
        project = self.pht.p

        project.sync_layouts()

        # Ensure that all machine nodes are in corresponding lists
        for d in project.descriptions:
            if isinstance(d, MachineNode):
                d.link(handle_system_bus = False)

        pythonize(project, file_name)

    def co_rsp_poller(self):
        rt = self.rt
        target = rt.target

        target.run_no_block()

        target.finished = False
        target._interrupt = False
        while not target._interrupt:
            yield
            try:
                target.poll()
            except:
                print_exc()
                print("Target PC 0x%x" % (rt.get_reg(rt.pc)))
                break

        yield

        if not target.finished:
            target.finished = True
            target.rsp.finish()

        # self.destroy()


def main():
    ap = QArgumentParser(
        description = "QEMU runtime introspection tool"
    )
    ap.add_argument("qarg",
        nargs = "+",
        help = "QEMU executable and arguments to it. Prefix them with `--`."
    )
    args = ap.parse_args()

    # executable
    qemu_cmd_args = args.qarg

    # debug info
    qemu_debug = qemu_cmd_args[0]

    elf = InMemoryELFFile(qemu_debug)
    if not elf.has_dwarf_info():
        stderr("%s does not have DWARF info. Provide a debug QEMU build\n" % (
            qemu_debug
        ))
        return -1

    di = elf.get_dwarf_info()

    if di.pubtypes is None:
        print("%s does not contain .debug_pubtypes section. Provide"
            " -gpubnames flag to the compiller" % qemu_debug
        )

    dic = DWARFInfoCache(di,
        symtab = elf.get_section_by_name(b".symtab")
    )

    qomtg = QOMTreeGetter(dic,
        # verbose = True,
        interrupt = False
    )
    if "-i386" in qemu_debug or "-x86_64" in qemu_debug:
        MWClass = PCMachineWatcher
    else:
        MWClass = MachineWatcher

    mw = MWClass(dic, qomtg.tree,
        # verbose = True
    )

    mach_desc = MachineNode("runtime-machine", "")
    proj = GUIProject(
        descriptions = [mach_desc]
    )
    pht = GUIProjectHistoryTracker(proj, proj.history)

    MachineReverser(mw, mach_desc, pht)

    # auto select free port for gdb-server
    for port in range(4321, 1 << 16):
        test_socket = socket(AF_INET, SOCK_STREAM)
        try:
            test_socket.bind(("", port))
        except:
            pass
        else:
            break
        finally:
            test_socket.close()

    qemu_debug_addr = "localhost:%u" % port

    qemu_proc = Process(
        target = system,
        # XXX: if there are spaces in arguments this code will not work.
        args = (" ".join(["gdbserver", qemu_debug_addr] + qemu_cmd_args),)
    )

    qemu_proc.start()

    qemu_debugger = AMD64(qemu_debug_addr,
        # verbose = True,
        host = True
    )

    rt = Runtime(qemu_debugger, dic)

    mw.init_runtime(rt)
    qomtg.init_runtime(rt)

    tk = QEmuWatcherGUI(pht, mach_desc, rt)

    tk.geometry("1024x1024")
    tk.mainloop()

    if not qemu_debugger.finished:
        qemu_debugger.rsp.finish()

    # XXX: on_finish method is not called by RemoteTarget
    qomtg.to_file("qom-by-q.i.dot")

    qemu_proc.join()


def runtime_based_var_getting(rt):
    target = rt.target

    def type_reg(resumes = [1]):
        print("type reg")
        info = rt["info"]
        name = info["name"]
        parent = info["parent"]

        p_name = parent.fetch(target.address_size)
        print("parent name at 0x%0*x" % (target.tetradsize, p_name))

        print("%s -> %s" % (parent.fetch_c_string(), name.fetch_c_string()))

        rt.on_resume()

        if resumes[0] == 0:
            rt.target.interrupt()
        else:
            resumes[0] -= 1

    return type_reg


def explicit_var_getting(rt, object_c):
    dic = rt.dic
    target = rt.target
    # get info argument of type_register_internal function
    dic.account_subprograms(object_c)
    type_register_internal = dic.subprograms["type_register_internal"][0]
    info = type_register_internal.data["info"]

    print("info loc: %s" % info.location)

    def type_reg_fields():
        print("type reg")
        info_loc = info.location.eval(rt)
        info_loc_str = "%0*x" % (target.tetradsize, info_loc)
        print("info at 0x%s" % info_loc_str)
        info_val = switch_endian(
            decode_data(
                target.get_mem(info_loc_str, 8)
            )
        )
        print("info = 0x%s" % info_val)
        pt = dic.type_by_die(info.type_DIE)
        t = pt.target()
        print("info type: %s %s" % (" ".join(t.modifiers), t.name))
        # t is `typedef`, get the structure
        st = t.target()

        for f in st.fields():
            print("%s %s; // %s" % (f.type.name, f.name, f.location))

    def type_reg_name():
        v = Value(info, rt)
        name = v["name"]
        parent = v["parent"]

        p_name = parent.fetch(target.address_size)
        print("parent name at 0x%0*x" % (target.tetradsize, p_name))

        print("%s -> %s" % (parent.fetch_c_string(), name.fetch_c_string()))

    def type_reg():
        type_reg_name()
        type_reg_fields()

        rt.on_resume()

    return type_reg


def test_call_frame(type_register_internal, br_addr):
    frame = type_register_internal.frame_base

    print("frame base: %s" % frame)

    fde = dia.fde(br_addr)
    print("fde = %s" % fde)

    table_desc = fde.get_decoded()
    table = table_desc.table

    for row in table:
        print(row)

    call_frame_row = dia.cfr(br_addr)
    print("call frame: %s" % call_frame_row)
    cfa = dia.cfa(br_addr)
    print("CFA: %s" % cfa)


def test_subprograms(dic):
    cpu_exec = dic.get_CU_by_name("cpu-exec.c")

    # For testing:
    # pthread_atfork.c has subprogram data referencing location lists
    # ioport.c contains inlined subprogram, without ranges

    for cu in [cpu_exec]: # dic.iter_CUs():
        print(cu.get_top_DIE().attributes["DW_AT_name"].value)
        sps = dic.account_subprograms(cu)
        for sp in sps:
            print("%s(%s) -> %r" % (
                sp.name,
                ", ".join(varname for (varname, var) in sp.data.items()
                          if var.is_argument
                ),
                sp.ranges
            ))

            for varname, var in sp.data.items():
                print("    %s = %s" % (varname, var.location))


def test_line_program_sizes(dia):
    for cu in dia.iter_CUs():
        name = cu.get_top_DIE().attributes["DW_AT_name"].value
        print("Getting line program for %s" % name)
        li = dia.di.line_program_for_CU(cu)
        entries = li.get_entries()
        print("Prog size: %u" % len(entries))


def test_line_program(dia):
    cpu_exec = dia.get_CU_by_name("cpu-exec.c")

    lp = dia.di.line_program_for_CU(cpu_exec)
    entrs = lp.get_entries()

    print("%s line program (%u)" % (
        cpu_exec.get_top_DIE().attributes["DW_AT_name"].value,
        len(entrs)
    ))
    # print("\n".join(repr(e.state) for e in entrs))

    dia.account_line_program(lp)
    lmap = dia.find_line_map("cpu-exec.c")

    for (l, r), entries in lmap.items():
        s = entries[0].state
        print("[%6i;%6i]: %s 0x%x" % (
            1 if l is None else l,
            r - 1,
            "S" if s.is_stmt else " ",
            s.address
        ))


def test_CU_lookup(dia):
    dia.get_CU_by_name("tcg.c")
    print("found tcg.c")
    dia.get_CU_by_name(join("ui", "console.c"))
    print("found ui/vl.c")
    dia.get_CU_by_name(join("ui", "console.c"))
    print("found ui/vl.c again")
    dia.get_CU_by_name("console.c")
    print("found console.c")
    try:
        dia.get_CU_by_name("virtio-blk.c")
    except:
        dia.get_CU_by_name(join("block", "virtio-blk.c"))
        print("found block/virtio-blk.c")
    else:
        print("short suffix exception is expected")
    try:
        dia.get_CU_by_name("apic.c")
    except:
        dia.get_CU_by_name(join("kvm", "apic.c"))
        print("found kvm/apic.c")
    else:
        print("short suffix exception is expected")


def test_cache(qemu_debug):
    cache_file = qemu_debug + ".qec"

    if isfile(cache_file):
        print("Trying to load cache from %s" % cache_file)
        try:
            execfile(cache_file, globals = loaded)
        except:
            stderr.write("Cache file execution error:\n")
            print_exc()

        cache = loaded.get("qec", None)

        print("Cache was %sloaded." % ("NOT " if cache is None else ""))
    else:
        cache = None

    if cache is None:
        print("Building cache of %s" % qemu_debug)
        cache = QELFCache(qemu_debug)

        print("Saving cache to %s" % cache_file)
        with open(cache_file, "wb") as f:
            PyGenerator().serialize(f, cache)


if __name__ == "__main__":
    exit(main())