from .settings_window import \
    SettingsWidget, \
    SettingsWindow

from common import \
    mlget as _

from .var_widgets import \
    VarCheckbutton, \
    VarLabel

from qemu import \
    MachineNodeOperation, \
    MemoryNode, \
    MemoryLeafNode, \
    MemoryAliasNode, \
    MemoryRAMNode, \
    MemoryROMNode, \
    MOp_AddMemChild, \
    MOp_RemoveMemChild, \
    MOp_SetMemNodeAttr, \
    MOp_SetMemNodeAlias

from six.moves.tkinter import \
    StringVar, \
    BooleanVar

from six.moves.tkinter_ttk import \
    Combobox

from .device_settings import \
    DeviceSettingsWidget

from .hotkey import \
    HKEntry

class MemorySettingsWidget(SettingsWidget):
    def __init__(self, mem, *args, **kw):
        SettingsWidget.__init__(self, *args, **kw)

        self.mem = mem

        self.columnconfigure(0, weight = 0)
        self.columnconfigure(1, weight = 1)
        self.rowconfigure(0, weight = 0)
        row = 0

        l = VarLabel(self, text = _("Region type"))
        l.grid(row = row, column = 0, sticky = "NES")
        
        memtype2str = {
           MemoryNode: _("Container"),
           MemoryAliasNode: _("Alias"),
           MemoryRAMNode: _("RAM"),
           MemoryROMNode: _("ROM")
        }

        l = VarLabel(self, text = memtype2str[ type(mem) ])
        l.grid(row = row, column = 1, sticky = "NEWS")
        row += 1

        l = VarLabel(self, text = _("Parent region"))
        l.grid(row = row, column = 0, sticky = "NES")

        self.var_parent = StringVar()
        self.cb_parent = Combobox(self,
            textvariable = self.var_parent,
            state = "readonly"
        )
        self.cb_parent.grid(row = row, column = 1, sticky = "NEWS")
        row += 1

        self.fields = [
            (_("Name"), "name", str),
            (_("Size"), "size", int),
            (_("Offset"), "offset", int),
            (_("May overlap"), "may_overlap", bool),
            (_("Priority"), "priority", int)
        ]

        if type(mem) is MemoryAliasNode:
            self.fields.extend([ (_("Alias offset"), "alias_offset", int) ])

        for text, field, _type in self.fields:
            if _type is bool:
                l = None
                v = BooleanVar()
                w = VarCheckbutton(self, text = text, variable = v)
            else:
                l = VarLabel(self, text = text)
                v = StringVar()
                w = HKEntry(self, textvariable = v)

            self.rowconfigure(row, weight = 0)
            if l is None:
                w.grid(row = row, column = 0, sticky = "NWS",
                    columnspan = 2
                )
            else:
                l.grid(row = row, column = 0, sticky = "NES")
                w.grid(row = row, column = 1, sticky = "NEWS")
            row += 1

            setattr(self, "w_" + field, w)
            setattr(self, "var_" + field, v)

        if type(mem) is MemoryAliasNode:
            l = VarLabel(self, text = _("Alias region"))
            l.grid(row = row, column = 0, sticky = "NES")

            self.var_alias_to = StringVar()
            self.cb_alias_to = Combobox(self,
                textvariable = self.var_alias_to,
                state = "readonly"
            )
            self.cb_alias_to.grid(row = row, column = 1, sticky = "NEWS")

    def __apply_internal__(self):
        prev_pos = self.mht.pos

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
                self.mht.stage(MOp_RemoveMemChild, self.mem.id, cur_parent_id)
            if not new_parent_id == -1:
                self.mht.stage(MOp_AddMemChild, self.mem.id, new_parent_id)

        for text, field, _type in self.fields:
            new_val = getattr(self, "var_" + field).get()
            if _type is int:
                try:
                    new_val = int(new_val, base = 0)
                except ValueError:
                    pass

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

        if prev_pos is not self.mht.pos:
            self.mht.set_sequence_description(
                _("Memory '%s' (%d) configuration.") % (
                    self.mem.name, self.mem.id
                )
            )

    def refresh(self):
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
            if _type is int:
                try:
                    cur_val = hex(cur_val)
                except TypeError:
                    pass

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

    def on_changed(self, op, *args, **kw):
        if not isinstance(op, MachineNodeOperation):
            return

        if op.writes_node() and self.mem.id == -1:
            self.destroy()
        else:
            self.refresh()

class MemorySettingsWindow(SettingsWindow):
    def __init__(self, mem, *args, **kw):
        SettingsWindow.__init__(self, *args, **kw)

        self.title(_("Memory settings"))

        self.set_sw(MemorySettingsWidget(mem, self.mach, self))
        self.sw.grid(row = 0, column = 0, sticky = "NEWS")
