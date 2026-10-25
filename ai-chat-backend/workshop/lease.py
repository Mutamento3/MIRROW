"""Hold one local OS lease before recovery/dispatch; stdio is only a proxy."""
import os


class WriterLease:
    def __init__(self, path):
        self.file = open(path, 'a+b')
        self.file.seek(0)
        self.file.write(b'0')
        self.file.flush()
        self.file.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise RuntimeError('workshop_writer_already_running') from None

    def close(self):
        if not self.file.closed:
            self.file.close()
