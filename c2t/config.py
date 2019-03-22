__all__ = [
    "C2TConfig"
  , "Run"
  , "get_new_rsp"
  , "DebugClient"
  , "DebugServer"
  , "TestBuilder"
]

from collections import (
    namedtuple
)
from common import (
    pypath
)
with pypath("..pyrsp"):
    from pyrsp.rsp import (
        RSP,
        archmap
    )

# CPU Testing Tool configuration components
C2TConfig = namedtuple(
    "C2TConfig",
    "rsp_target qemu gdbserver target_compiler oracle_compiler"
)
Run = namedtuple(
    "Run",
    "executable args"
)


def get_new_rsp(regs, pc, regsize, little_endian = True):
    class CustomRSP(RSP):

        def __init__(self, *a, **kw):
            self.arch = dict(
                regs = regs,
                endian = little_endian,
                bitsize = regsize
            )
            self.pc_reg = pc
            super(CustomRSP, self).__init__(*a, **kw)
    return CustomRSP


class DebugClient(object):

    def __init__(self, march, new_rsp = None, sp = None, qemu_reset = False):
        self.march = march
        if march in archmap:
            self.rsp = archmap[march]
        elif new_rsp is not None:
            self.rsp = new_rsp
        else:
            self.rsp = None
        self.sp = sp
        self.qemu_reset = qemu_reset


class DebugServer(object):

    def __init__(self, run):
        self.run = run

    @property
    def run_script(self):
        return ' '.join(self.run)


class TestBuilder(object):

    def __init__(self,
        compiler = None,
        frontend = None,
        backend = None,
        linker = None
    ):
        self.compiler = compiler
        self.frontend = frontend
        self.backend = backend
        self.linker = linker
        self.runs = (
            filter(
                lambda v: v is not None,
                list([self.compiler, self.frontend, self.backend, self.linker])
            )
        )

    # TODO: how process when runs = []
    @property
    def run_script(self):
        for run in self.runs:
            yield ' '.join(run)