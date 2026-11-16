import os
import time

pid = os.fork()
if pid == 0:
    os._exit(0)
else:
    print(f"child pid: {pid}")
    time.sleep(40)
    os.waitpid(pid, 0)
    print("reaped")
