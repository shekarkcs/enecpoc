import time
from .db import init
from .services import work_once

if __name__ == '__main__':
    init()
    while True:
        if not work_once():
            time.sleep(1)
