from qemu import \
    ProjectOperation

from copy import \
    deepcopy

from common import \
    mlget as _

from gui_project import \
    GUIProject

""" The difference is the project should be a _GUI_ project. """
class GUIProjectOperation(ProjectOperation):
    def __init__(self, project, *args, **kw):
        if not isinstance(project, GUIProject):
            raise Exception("The 'project' argument is not a GUI project.")

        ProjectOperation.__init__(self, project, *args, **kw)

class GUIDescriptionOperation(GUIProjectOperation):
    def __init__(self, description, *args, **kw):
        GUIProjectOperation.__init__(self, *args, **kw)

        self.desc_name = str(description.name)

    def find_desc(self):
        return self.p.find(name = self.desc_name).next()

class POp_SetDescLayout(GUIDescriptionOperation):
    def __init__(self, new_layout, *args, **kw):
        GUIDescriptionOperation.__init__(self, *args, **kw)

        if new_layout is not None:
            self.new_layout = deepcopy(new_layout)
        else:
            self.new_layout = None

    def __backup__(self):
        layouts = self.p.get_layout_objects(self.desc_name)

        if layouts:
            self.prev_layouts = deepcopy(layouts)
        else:
            self.prev_layouts = None

    def __do__(self):
        if self.prev_layouts is not None:
            self.p.delete_layouts(self.desc_name)
        if self.new_layout is not None:
            self.p.add_layout_object(deepcopy(self.new_layout))

    def __undo__(self):
        if self.new_layout is not None:
            self.p.delete_layouts(self.desc_name)
        if self.prev_layouts is not None:
            self.p.add_layout_objects(deepcopy(self.prev_layouts))

    def __description__(self):
        if self.prev_layouts is None:
            return _("Set layout for '%s' description representation.") % \
                self.desc_name
        elif self.new_layout is None:
            return _("Remove layouts of '%s' description representation.") % \
                self.desc_name
        else:
            return _("Replace layout of '%s' description representation.") % \
                self.desc_name
