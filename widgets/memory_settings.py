from .settings_window import (
    SettingsWidget,
    SettingsWindow
)
from common import (
    mlget as _
)
from .var_widgets import (
    VarCheckbutton,
    VarLabel
)
from qemu import (
    QemuTypeName,
    MachineNodeOperation,
    MemoryNode,
    MemorySASNode,
    MemoryLeafNode,
    MemoryAliasNode,
    MemoryRAMNode,
    MemoryROMNode,
    MOp_AddMemChild,
    MOp_RemoveMemChild,
    MOp_SetMemNodeAttr,
    MOp_SetMemNodeAlias
)
from six.moves.tkinter import (
    BOTH,
    StringVar,
    BooleanVar
)
from six.moves.tkinter_ttk import (
    Combobox
)
from .device_settings import (
    DeviceSettingsWidget
)
from .hotkey import (
    HKEntry
)
from .gui_frame import (
    GUIFrame
)
from source import (
    CConst
)

def name_to_var_base(name):
    type_base = "sas" if "System address space" in name else name
    qtn = QemuTypeName(type_base)
    return qtn.for_id_name

class MemorySettingsWidget(SettingsWidget):
    def __init__(self, mem, *args, **kw):
        SettingsWidget.__init__(self, mem, *args, **kw)

        self.mem = mem

        self.mem_fr = fr = GUIFrame(self)
        fr.pack(fill = BOTH, expand = False)

        fr.columnconfigure(0, weight = 0)
        fr.columnconfigure(1, weight = 1)
        fr.rowconfigure(0, weight = 0)
        row = 0

        l = VarLabel(fr, text = _("Region type"))
        l.grid(row = row, column = 0, sticky = "NES")

        memtype2str = {
           MemoryNode: _("Container"),
           MemorySASNode: _("SAS"),
           MemoryAliasNode: _("Alias"),
           MemoryRAMNode: _("RAM"),
           MemoryROMNode: _("ROM")
        }

        l = VarLabel(fr, text = memtype2str[ type(mem) ])
        l.grid(row = row, column = 1, sticky = "NEWS")
        row += 1

        if not isinstance(mem, MemorySASNode):
            l = VarLabel(fr, text = _("Parent region"))
            l.grid(row = row, column = 0, sticky = "NES")

            self.var_parent = StringVar()
            self.cb_parent = Combobox(fr,
                textvariable = self.var_parent,
                state = "readonly"
            )
            self.cb_parent.grid(row = row, column = 1, sticky = "NEWS")
            row += 1

        self.fields = [
            (_("Name"), "name", CConst),
            (_("Size"), "size", CConst),
            (_("Offset"), "offset", CConst),
            (_("May overlap"), "may_overlap", bool),
            (_("Priority"), "priority", CConst)
        ]

        if type(mem) is MemoryAliasNode:
            self.fields.extend([ (_("Alias offset"), "alias_offset", CConst) ])

        if isinstance(mem, MemorySASNode):
            self.fields = [(_("Name"), "name", str)]

        for text, field, _type in self.fields:
            if _type is bool:
                l = None
                v = BooleanVar()
                w = VarCheckbutton(fr, text = text, variable = v)
            else:
                l = VarLabel(fr, text = text)
                v = StringVar()
                w = HKEntry(fr, textvariable = v)

            fr.rowconfigure(row, weight = 0)
            if l is None:
                w.grid(row = row, column = 0, sticky = "NWS",
                    columnspan = 2
                )
            else:
                l.grid(row = row, column = 0, sticky = "NES")
                l.gi = l.grid_info()
                w.grid(row = row, column = 1, sticky = "NEWS")
            w.gi = w.grid_info()
            row += 1

            if l:
                setattr(self, "l_" + field, l)
            setattr(self, "w_" + field, w)
            setattr(self, "var_" + field, v)

        self.var_name.trace_variable("w", self.__on_name_var_changed)

        if type(mem) is MemoryAliasNode:
            l = VarLabel(fr, text = _("Alias region"))
            l.grid(row = row, column = 0, sticky = "NES")

            self.var_alias_to = StringVar()
            self.cb_alias_to = Combobox(fr,
                textvariable = self.var_alias_to,
                state = "readonly"
            )
            self.cb_alias_to.grid(row = row, column = 1, sticky = "NEWS")

        if not isinstance(mem, MemorySASNode):
            if not mem.parent:
                self.l_offset.grid_forget()
                self.w_offset.grid_forget()

    def __apply_internal__(self):
        if not isinstance(self.mem, MemorySASNode):
            new_parent = self.find_node_by_link_text(self.var_parent.get())
            cur_parent = self.mem.parent

            if new_parent is None:
                new_parent_id = -1
            else:
                new_parent_id = new_parent.id

            if cur_parent is None:
                cur_parent_id = -1
            else:
                cur_parent_id = cur_parent.id

            if not new_parent_id == cur_parent_id:
                if not cur_parent_id == -1:
                    self.mht.stage(
                        MOp_RemoveMemChild,
                        self.mem.id,
                        cur_parent_id
                    )
                if not new_parent_id == -1:
                    self.mht.stage(MOp_AddMemChild, self.mem.id, new_parent_id)

        for text, field, _type in self.fields:
            new_val = getattr(self, "var_" + field).get()
            if _type is CConst:
                try:
                    new_val = CConst.parse(new_val)
                except:
                    continue

            cur_val = getattr(self.mem, field)

            if new_val == cur_val:
                continue

            self.mht.stage(MOp_SetMemNodeAttr, field, new_val, self.mem.id)

        if type(self.mem) is MemoryAliasNode:
            new_alias_to = self.find_node_by_link_text(self.var_alias_to.get())
            cur_alias_to = self.mem.alias_to

            if not new_alias_to == cur_alias_to:
                self.mht.stage(
                    MOp_SetMemNodeAlias,
                    "alias_to",
                    new_alias_to,
                    self.mem.id)

        self.mht.set_sequence_description(
            _("Memory '%s' (%d) configuration.") % (
                self.mem.name, self.mem.id
            )
        )

    def refresh(self):
        SettingsWidget.refresh(self)

        if not isinstance(self.mem, MemorySASNode):
            values = [
                DeviceSettingsWidget.gen_node_link_text(mem) for mem in (
                    [ mem for mem in self.mach.mems if (
                        not isinstance(mem, MemoryLeafNode)
                        and mem != self.mem )
                    ] + [ None ]
                )
            ]

            self.cb_parent.config(values = values)

            self.var_parent.set(
                DeviceSettingsWidget.gen_node_link_text(self.mem.parent)
            )

        for text, field, _type in self.fields:
            var = getattr(self, "var_" + field)
            cur_val = getattr(self.mem, field)
            var.set(cur_val)

        if type(self.mem) is MemoryAliasNode:
            values = [
                DeviceSettingsWidget.gen_node_link_text(mem) for mem in (
                    [ mem for mem in self.mach.mems if (mem != self.mem ) ]
                )
            ]

            self.cb_alias_to.config(values = values)

            self.var_alias_to.set(
                DeviceSettingsWidget.gen_node_link_text(self.mem.alias_to)
            )

        if not isinstance(self.mem, MemorySASNode):
            if self.mem.parent is None:
                self.l_offset.grid_forget()
                self.w_offset.grid_forget()
            else:
                self.l_offset.grid(self.l_offset.gi)
                self.w_offset.grid(self.w_offset.gi)

    def on_changed(self, op, *args, **kw):
        if not isinstance(op, MachineNodeOperation):
            return

        if op.writes_node() and self.mem.id == -1:
            self.destroy()
        else:
            self.refresh()

    def __on_name_var_changed(self, *args):
        vvb = self.v_var_base
        vb = vvb.get()

        try:
            prev_n = self.__prev_name
        except AttributeError:
            # name was not edited yet
            prev_n = self.mem.name.v

        if vb == "mem" or vb == name_to_var_base(prev_n):
            """ If current variable name base is default or corresponds to
            previous name then auto suggest new variable name base
            with account of just entered name. """
            n = self.var_name.get()
            vvb.set(name_to_var_base(n))
            self.__prev_name = n

class MemorySettingsWindow(SettingsWindow):
    def __init__(self, mem, *args, **kw):
        SettingsWindow.__init__(self, mem, *args, **kw)

        self.title(_("Memory settings"))

        self.set_sw(MemorySettingsWidget(mem, self.mach, self))
        self.sw.grid(row = 0, column = 0, sticky = "NEWS")
