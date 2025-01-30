from conan.api.output import Color, ConanOutput

class RunnerOutput(ConanOutput):
    def __init__(self, hostname: str):
        super().__init__()
        self.set_warnings_as_errors(True) # Make log errors blocker
        self._prefix = f"{hostname} | "

    def _write_message(self, msg, fg=None, bg=None, newline=True):
        super()._write_message(self._prefix, Color.BLACK, Color.BRIGHT_YELLOW, newline=False)
        super()._write_message(msg, fg, bg, newline)

    @property
    def padding(self):
        return len(self._prefix) + 1

