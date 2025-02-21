import os

from ayon_server.helpers.modules import classes_from_module, import_module
from ayon_server.logging import log_traceback

PATHS = [
    "/extensions",
    "/usr/share/extensions",
]


class ServerExtension:
    async def initialize(self):
        pass


async def init_extensions():
    for path in PATHS:
        if not os.path.isdir(path):
            continue

        for file in os.listdir(path):
            if file.startswith("_"):
                continue

            if os.path.isdir(f"{path}/{file}"):
                mpath = f"{path}/{file}/__init__.py"
                mname = f"{file}"
                run_main = False
            elif os.path.splitext(file)[1] == ".py":
                mpath = f"{path}/{file}"
                mname = os.path.splitext(file)[0]
                run_main = False
            elif os.path.splitext(file)[1] == ".so":
                mpath = f"{path}/{file}"
                mname = file.split(".")[0]
                run_main = True
            else:
                continue

            try:
                module = import_module(mname, mpath)
            except Exception:
                log_traceback(f"Unable to import extension module {mname}")
                continue

            try:
                if run_main:
                    await module.main()
                    continue

                classes = classes_from_module(ServerExtension, module)
                for cls in classes:
                    await cls().initialize()
            except Exception:
                log_traceback(f"Unable to initialize extension {mname}")
                continue
