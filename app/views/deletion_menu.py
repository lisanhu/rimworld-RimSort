import sys
from errno import ENOTEMPTY
from shutil import rmtree
from typing import Any, Callable

from loguru import logger
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu

from app.utils.generic import (
    attempt_chmod,
    delete_files_except_extension,
    delete_files_only_extension,
)
from app.utils.metadata import MetadataManager
from app.views.dialogue import (
    show_dialogue_conditional,
    show_warning,
)


class ModDeletionMenu(QMenu):
    def __init__(
        self,
        uuids: list[str],
        get_selected_uuids: Callable[[], list[str]],
        delete_mod: bool = True,
        delete_both: bool = True,
        delete_dds: bool = True,
    ):
        super().__init__(title="Deletion options")
        self.uuids = uuids
        self.get_selected_uuids = get_selected_uuids
        self.metadata_manager = MetadataManager.instance()
        if delete_mod:
            dma = QAction("Delete mod")
            dma.triggered.connect(self.delete_mod_keep_dds)
            self.addAction(dma)
        if delete_both:
            dba = QAction("Delete mod (keep .dds)")
            dba.triggered.connect(self.delete_both)
            self.addAction(dba)
        if delete_dds:
            dda = QAction("Delete optimized textures (.dds files only)")
            dda.triggered.connect(self.delete_dds)
            self.addAction(dda)

    def _delete_mods(self, fn: Callable, uuids: list[str]):
        steamcmd_acf_pfid_purge: set[str] = set()
        for uuid in uuids:
            mod_metadata = self.metadata_manager.internal_local_metadata[uuid]
            if mod_metadata[
                "data_source"  # Disallow Official Expansions
            ] != "expansion" or not mod_metadata["packageid"].startswith(
                "ludeon.rimworld"
            ):
                if fn(mod_metadata):
                    self.uuids.remove(uuid)
                    if mod_metadata.get("steamcmd"):
                        steamcmd_acf_pfid_purge.add(mod_metadata["publishedfileid"])

        # Purge any deleted SteamCMD mods from acf metadata
        if steamcmd_acf_pfid_purge:
            self.metadata_manager.steamcmd_purge_mods(
                publishedfileids=steamcmd_acf_pfid_purge
            )

    def delete_both(self):
        def _inner_delete_both(mod_metadata: dict[str, Any]) -> bool:
            try:
                rmtree(
                    mod_metadata["path"],
                    ignore_errors=False,
                    onexc=attempt_chmod,
                )
                return True
            except FileNotFoundError:
                logger.debug(
                    f"Unable to delete mod. Path does not exist: {mod_metadata['path']}"
                )
                return False
            except OSError as e:
                if sys.platform == "win32":
                    error_code = e.winerror
                else:
                    error_code = e.errno
                if e.errno == ENOTEMPTY:
                    warning_text = "Mod directory was not empty. Please close all programs accessing files or subfolders in the directory (including your file manager) and try again."
                else:
                    warning_text = "An OSError occurred while deleting mod."

                logger.warning(
                    f"Unable to delete mod located at the path: {mod_metadata['path']}"
                )
                show_warning(
                    title="Unable to delete mod",
                    text=warning_text,
                    information=f"{e.strerror} occurred at {e.filename} with error code {error_code}.",
                )
            return False

        uuids = self.get_selected_uuids()
        answer = show_dialogue_conditional(
            title="Are you sure?",
            text=f"You have selected {len(uuids)} mods for deletion.",
            information="\nThis operation delete a mod's directory from the filesystem."
            + "\nDo you want to proceed?",
        )
        if answer == "&Yes":
            self._delete_mods(_inner_delete_both, uuids)

    def delete_dds(self, uuids: list[str]):
        answer = show_dialogue_conditional(
            title="Are you sure?",
            text=f"You have selected {len(uuids)} mods to Delete optimized textures (.dds files only)",
            information="\nThis operation will only delete optimized textures (.dds files only) from mod files."
            + "\nDo you want to proceed?",
        )
        if answer == "&Yes":
            self._delete_mods(
                lambda mod_metadata: (
                    delete_files_only_extension(
                        directory=mod_metadata["path"],
                        extension=".dds",
                    ),
                    True,
                )[1],
                uuids,
            )

    def delete_mod_keep_dds(self, uuids: list[str]):
        answer = show_dialogue_conditional(
            title="Are you sure?",
            text=f"You have selected {len(uuids)} mods for deletion.",
            information="\nThis operation will recursively delete all mod files, except for .dds textures found."
            + "\nDo you want to proceed?",
        )
        if answer == "&Yes":
            self._delete_mods(
                lambda mod_metadata: (
                    delete_files_except_extension(
                        directory=mod_metadata["path"],
                        extension=".dds",
                    ),
                    True,
                )[1],
                uuids,
            )
