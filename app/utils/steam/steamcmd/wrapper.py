import os
import platform
import shutil
import sys
import tarfile
from io import BytesIO
from pathlib import Path
from tempfile import gettempdir
from typing import Any
from zipfile import ZipFile

import requests
from loguru import logger
from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QMessageBox

import app.utils.symlink as symlink
from app.controllers.settings_controller import SettingsController
from app.utils.event_bus import EventBus
from app.utils.generic import handle_remove_read_only
from app.utils.generic import rmtree as g_rmtree
from app.views.dialogue import (
    BinaryChoiceDialog,
    InformationBox,
    show_dialogue_conditional,
    show_fatal_error,
    show_warning,
)
from app.windows.runner_panel import RunnerPanel


class SteamcmdInterface:
    """
    Create SteamcmdInterface object to provide an interface for SteamCMD functionality
    """

    _instance: "None | SteamcmdInterface" = None

    def __new__(cls, *args: Any, **kwargs: Any) -> "SteamcmdInterface":
        if cls._instance is None:
            cls._instance = super(SteamcmdInterface, cls).__new__(cls)
        return cls._instance

    def __init__(self, steamcmd_prefix: str, validate: bool) -> None:
        if not hasattr(self, "initialized"):
            self.initialized = True
            self.setup = False
            self.steamcmd_prefix = steamcmd_prefix
            super(SteamcmdInterface, self).__init__()
            logger.debug("Initializing SteamcmdInterface")
            self.initialize_prefix(steamcmd_prefix, validate)

            EventBus().do_clear_steamcmd_depot_cache.connect(
                lambda: self.clear_depot_cache()
            )
            self.translate = QCoreApplication.translate
            logger.debug("Finished SteamcmdInterface initialization")

    def initialize_prefix(self, steamcmd_prefix: str, validate: bool) -> None:
        self.steamcmd_prefix = steamcmd_prefix
        self.steamcmd_install_path = str(Path(self.steamcmd_prefix) / "steamcmd")
        self.steamcmd_depotcache_path = str(
            Path(self.steamcmd_install_path) / "depotcache"
        )
        self.steamcmd_steam_path = str(Path(self.steamcmd_prefix) / "steam")
        self.system = platform.system()
        self.validate_downloads = validate

        if self.system == "Darwin":
            self.steamcmd_url = (
                "https://steamcdn-a.akamaihd.net/client/installer/steamcmd_osx.tar.gz"
            )
            self.steamcmd = str((Path(self.steamcmd_install_path) / "steamcmd.sh"))
        elif self.system == "Linux":
            self.steamcmd_url = (
                "https://steamcdn-a.akamaihd.net/client/installer/steamcmd_linux.tar.gz"
            )
            self.steamcmd = str((Path(self.steamcmd_install_path) / "steamcmd.sh"))
        elif self.system == "Windows":
            self.steamcmd_url = (
                "https://steamcdn-a.akamaihd.net/client/installer/steamcmd.zip"
            )
            self.steamcmd = str((Path(self.steamcmd_install_path) / "steamcmd.exe"))
        else:
            show_fatal_error(
                "SteamcmdInterface",
                f"Found platform {self.system}. steamcmd is not supported on this platform.",
            )
            return

        if not os.path.exists(self.steamcmd_install_path):
            os.makedirs(self.steamcmd_install_path)
            logger.debug(
                f"SteamCMD does not exist. Creating path for installation: {self.steamcmd_install_path}"
            )

        if not os.path.exists(self.steamcmd_steam_path):
            os.makedirs(self.steamcmd_steam_path)
        self.steamcmd_appworkshop_acf_path = str(
            (
                Path(self.steamcmd_steam_path)
                / "steamapps"
                / "workshop"
                / "appworkshop_294100.acf"
            )
        )
        self.steamcmd_content_path = str(
            (Path(self.steamcmd_steam_path) / "steamapps" / "workshop" / "content")
        )

    @classmethod
    def instance(cls, *args: Any, **kwargs: Any) -> "SteamcmdInterface":
        if cls._instance is None:
            cls._instance = cls(*args, **kwargs)
        elif args or kwargs:
            raise ValueError("SteamcmdInterface instance has already been initialized.")
        return cls._instance

    @staticmethod
    def create_symlink(
        src_path: str,
        dst_path: str,
        force: bool = False,
        show_dialogues: bool = True,
        runner: RunnerPanel | None = None,
    ) -> bool:
        """
        Creates a symlink/junction from src_path to dst_path.

        Note that this method will not convert relative paths to absolute paths before system calls.
        To ensure that compatibility with Windows, src_path must exist and be a directory.

        If the dst_path exists and force is False:
            - If dst_path is a symlink/junction, it will be unlinked re-created based on method args.
            - If dst_path is a directory and empty, it will be deleted.
            - If dst_path is a directory and not empty, it will safely fail and return False.
            - If dst_path is a file, it will safely fail and return False.
        If the dst_path exists and force is True:
            - dst_path will be removed (even if it is a non-empty directory) and re-created based on method args, even if it already exists.

        :param src_path: The source path/target to create the symlink from. Must be a directory
        :type src_path: str
        :param dst_path: The destination path to create the symlink to.
        :type dst_path: str
        :param force: Force the creation of the symlink/junction, even if the dst_path exists. Default is False.
        :type force: bool
        :param show_dialogues: Show conditional dialogues to the user on fixable failures. Default is True.
        :type show_dialogues: bool
        :param runner: A RunnerPanel to interact with. Default is None.
        :type runner: RunnerPanel
        :return: True if the symlink/junction was created successfully. False otherwise.
        """
        if runner is not None:
            runner.message(f"[{src_path}] -> " + dst_path)

        try:
            symlink.create_symlink(src_path, dst_path, force=force)
            return True
        except symlink.SymlinkDstNotEmptyError as e:
            return SteamcmdInterface._create_symlink_retry(
                src_path,
                dst_path,
                show_dialogues,
                force,
                "The symlink destination exists and is a non-empty directory.",
                "Would you like to delete the existing directory and its contents and retry creating the symlink?",
                "Delete Directory and Retry",
                e,
                runner,
            )

        except symlink.SymlinkDstIsFileError as e:
            return SteamcmdInterface._create_symlink_retry(
                src_path,
                dst_path,
                show_dialogues,
                force,
                "The symlink destination exists and is a file.",
                "Would you like to delete the existing file and retry creating the symlink?",
                "Delete File and Retry",
                e,
                runner,
            )

        except symlink.SymlinkDstParentNotExistError as e:
            return SteamcmdInterface._create_symlink_retry(
                src_path,
                dst_path,
                show_dialogues,
                force,
                "The symlink destination parent directory does not exist.",
                "Would you like to create the parent directory and retry creating the symlink?",
                "Create Parent Directory and Retry",
                e,
                runner,
            )

        except Exception as e:
            if runner is not None:
                runner.message(
                    f"Failed to create symlink. Error: {type(e).__name__}: {str(e)}"
                )
            show_warning(
                "Failed to Create Symlink",
                f"Failed to create symlink for {sys.platform}",
                details=f"Error: {type(e).__name__}: {str(e)}",
            )

            return False

    @staticmethod
    def _create_symlink_retry(
        src_path: str,
        dst_path: str,
        show_dialogues: bool,
        force: bool,
        choice_text: str,
        choice_info: str,
        choice_postive_text: str,
        e: Exception,
        runner: RunnerPanel | None = None,
    ) -> bool:
        """Helper function to check if the appropriate to ask if the user wants to retry creating a symlink,
        and to ask if the user wants to retry creating a symlink.

        If force is True this method will return False as the mitigation would have to just retry with force set as True.
        If show_dialogues is False this method will return False as the choice dialogues is not to be shown.

        If the user chooses to retry, this method will call create_symlink with force set as True and return the result.

        :param src_path: The source path/target to create the symlink from. Must be a directory
        :type src_path: str
        :param dst_path: The destination path to create the symlink to.
        :type dst_path: str
        :param force: Force the creation of the symlink/junction, even if the dst_path exists. Default is False.
        :type force: bool
        :param show_dialogues: Show conditional dialogues to the user on fixable failures. Default is True.
        :type show_dialogues: bool
        :param choice_text: Choice text for the dialogue
        :type choice_text: str
        :param choice_info: Choice info for the dialogue
        :type choice_info: str
        :param choice_postive_text: Choice positive text for the dialogue
        :type choice_postive_text: str
        :param e: The exception that was raised
        :type e: Exception
        :param runner: A RunnerPanel to interact with. Default is None.
        :type runner: RunnerPanel
        :return: True if the symlink/junction was created successfully. False otherwise.
        :rtype: bool
        """
        msg = f"Failed to create symlink. Error: {type(e).__name__}: {str(e)}"
        if runner is not None:
            runner.message(msg)

        if not show_dialogues or force:
            return False

        diag = BinaryChoiceDialog(
            title="Symlink Creation Failed",
            text=choice_text,
            information=choice_info,
            details=msg,
            positive_text=choice_postive_text,
        )

        if diag.exec_is_positive():
            return SteamcmdInterface.create_symlink(
                src_path,
                dst_path,
                force=True,
                show_dialogues=show_dialogues,
                runner=runner,
            )

        return False

    def download_mods(
        self,
        publishedfileids: list[str],
        runner: RunnerPanel,
        clear_cache: bool = False,
    ) -> None:
        """
        This function downloads a list of mods from a list publishedfileids

        https://developer.valvesoftware.com/wiki/SteamCMD

        :param appid: a Steam AppID to pass to steamcmd
        :param publishedfileids: list of publishedfileids
        :param runner: a RimSort RunnerPanel to interact with
        :param clear_cache: whether to clear the steamcmd depot cache before downloading
        """
        runner.message("Checking for steamcmd...")
        if self.setup:
            runner.message(
                f"Got it: {self.steamcmd}\n"
                + f"Downloading list of {str(len(publishedfileids))} "
                + f"publishedfileids to: {self.steamcmd_steam_path}"
            )
            if clear_cache:
                self.clear_depot_cache(runner=runner)

            script = [
                f'force_install_dir "{self.steamcmd_steam_path}"',
                "login anonymous",
            ]
            download_cmd = "workshop_download_item 294100"
            for publishedfileid in publishedfileids:
                if self.validate_downloads:
                    script.append(f"{download_cmd} {publishedfileid} validate")
                else:
                    script.append(f"{download_cmd} {publishedfileid}")
            script.extend(["quit\n"])
            script_path = str((Path(gettempdir()) / "steamcmd_script.txt"))
            with open(script_path, "w", encoding="utf-8") as script_output:
                script_output.write("\n".join(script))
            runner.message(f"Compiled & using script: {script_path}")
            runner.execute(
                self.steamcmd,
                [f'+runscript "{script_path}"'],
                len(publishedfileids),
            )
        else:
            runner.message("SteamCMD was not found. Please setup SteamCMD first!")
            self.on_steamcmd_not_found(runner=runner)

    def check_for_steamcmd(self, prefix: str) -> bool:
        executable_name = os.path.split(self.steamcmd)[1] if self.steamcmd else None
        if executable_name is None:
            return False
        return os.path.exists(str(Path(prefix) / "steamcmd" / executable_name))

    def on_steamcmd_not_found(
        self,
        runner: RunnerPanel | None = None,
        ask_ignore: bool = False,
        settings_controller: SettingsController | None = None,
    ) -> bool:
        """Asks if the user wants to setup SteamCMD. If the user chooses to ignore the dialogue, set the steamcmd ignore flag in the settings.

        :param runner: The runner, defaults to None
        :type runner: RunnerPanel | None, optional
        :param ask_ignore: Whether to ask the user to ignore the dialogue, defaults to False
        :type ask_ignore: bool, optional
        :param settings_controller: The settings controller used to set steamcmd ignore flag, defaults to None
        :type settings_controller: SettingsController | None, optional
        :return: Whenever or not the user chose to ignore the dialogue
        :rtype: bool
        """
        if ask_ignore:
            btn_text = ["&Yes", "&No", "&Don't Ask Again"]
        else:
            btn_text = ["&Yes", "&No"]

        # Translate button texts explicitly before passing to show_dialogue_conditional
        translated_btn_text = [
            self.translate("SteamcmdInterface", btn) for btn in btn_text
        ]
        answer = show_dialogue_conditional(
            title=self.translate("SteamcmdInterface", "RimSort - SteamCMD setup"),
            text=self.translate(
                "SteamcmdInterface",
                "RimSort was unable to find SteamCMD installed in the configured prefix:\n",
            ),
            information=f"{self.steamcmd_prefix if self.steamcmd_prefix else '<None>'}\n\n"
            + self.translate("SteamcmdInterface", "Do you want to setup SteamCMD?"),
            button_text_override=translated_btn_text,
        )
        yes_text = self.translate("SteamcmdInterface", "&Yes")
        dont_ask_text = self.translate("SteamcmdInterface", "&Don't Ask Again")

        if answer == yes_text:
            EventBus().do_install_steamcmd.emit()
        if runner:
            runner.close()

        if ask_ignore and answer == dont_ask_text:
            if settings_controller is not None:
                settings_controller.active_instance.steamcmd_ignore = True
                settings_controller.settings.save()

            return True
        return False

    def clear_depot_cache(self, runner: RunnerPanel | None = None) -> bool:
        """Clears the steamCMD depot cache.
        Potential workaround for certain weird steamCMD behavior and download failures.

        :param runner: Runner panel if there is one., defaults to None
        :type runner: RunnerPanel | None, optional
        :exception Exception: If the depot cache cannot be cleared

        """
        logger.info("Attempting steamCMD depot cache clear")
        if not self.setup:
            if runner is not None:
                runner.message(
                    "Tried clearing depot cache but SteamCMD was not found. Please setup SteamCMD first!"
                )

            self.on_steamcmd_not_found(runner=runner)
            return False

        depot_cache = Path(self.steamcmd_install_path + "/depotcache")
        if not os.path.exists(depot_cache):
            logger.info(
                f"Skipping depot cache clear. Could not find cache: {depot_cache}"
            )
            if runner is not None:
                runner.message(
                    f"Skipping depot cache clear. Could not find cache: {depot_cache}"
                )
            else:
                InformationBox(
                    title=self.translate("SteamcmdInterface", "Depot Cache Cleared"),
                    text=self.translate(
                        "SteamcmdInterface", "SteamCMD depot cache was already cleared."
                    ),
                ).exec()
            return False

        if g_rmtree(depot_cache):
            logger.info("Depot cache cleared")
            if runner is not None:
                runner.message("Depot cache cleared")
            else:
                InformationBox(
                    title=self.translate("SteamcmdInterface", "Depot Cache Cleared"),
                    text=self.translate(
                        "SteamcmdInterface", "SteamCMD depot cache has been cleared."
                    ),
                ).exec()
            return True

        logger.error("Failed to clear depot cache")
        if runner is not None:
            runner.message("Failed to clear depot cache")

        return False

    def setup_steamcmd(
        self, symlink_source_path: str, reinstall: bool, runner: RunnerPanel
    ) -> None:
        installed = None
        if reinstall:
            runner.message("Existing steamcmd installation found!")
            runner.message(
                f"Deleting existing installation from: {self.steamcmd_install_path}"
            )
            shutil.rmtree(
                self.steamcmd_install_path,
                ignore_errors=False,
                onerror=handle_remove_read_only,
            )
            os.makedirs(self.steamcmd_install_path)
        if not self.check_for_steamcmd(prefix=self.steamcmd_prefix):
            try:
                runner.message(
                    f"Downloading & extracting steamcmd release from: {self.steamcmd_url}"
                )
                if ".zip" in self.steamcmd_url:
                    with ZipFile(
                        BytesIO(requests.get(self.steamcmd_url).content)
                    ) as zipobj:
                        zipobj.extractall(self.steamcmd_install_path)
                    runner.message("Installation completed")
                    installed = True
                elif ".tar.gz" in self.steamcmd_url:
                    with (
                        requests.get(self.steamcmd_url, stream=True) as rx,
                        tarfile.open(
                            fileobj=BytesIO(rx.content), mode="r:gz"
                        ) as tarobj,
                    ):
                        tarobj.extractall(self.steamcmd_install_path)
                    runner.message("Installation completed")
                    installed = True
            except Exception as e:
                runner.message("Installation failed")
                show_fatal_error(
                    "SteamcmdInterface",
                    f"Failed to download steamcmd for {self.system}",
                    "Did the file/url change?\nDoes your environment have access to the internet?",
                    details=f"Error: {type(e).__name__}: {str(e)}",
                )
        else:
            runner.message("SteamCMD already installed...")
            show_warning(
                "SteamcmdInterface",
                f"A steamcmd runner already exists at: {self.steamcmd}",
            )
            answer = show_dialogue_conditional(
                "Reinstall?",
                "Would you like to reinstall SteamCMD?",
                f"Existing install: {self.steamcmd_install_path}",
            )
            if answer == QMessageBox.StandardButton.Yes:
                runner.message(f"Reinstalling SteamCMD: {self.steamcmd_install_path}")
                self.setup_steamcmd(symlink_source_path, True, runner)
        if installed:
            if not os.path.exists(self.steamcmd_content_path):
                os.makedirs(self.steamcmd_content_path)
                runner.message(
                    f"Workshop content path does not exist. Creating for symlinking:\n\n{self.steamcmd_content_path}\n"
                )
            symlink_destination_path = str(
                (Path(self.steamcmd_content_path) / "294100")
            )
            runner.message(f"Symlink source : {symlink_source_path}")
            runner.message(f"Symlink destination: {symlink_destination_path}")
            if symlink.is_junction_or_link(
                symlink_destination_path
            ):  # Symlink/junction exists
                runner.message(
                    f"Symlink destination already exists! Please remove existing destination:\n\n{symlink_destination_path}\n"
                )
                answer = show_dialogue_conditional(
                    self.translate("SteamcmdInterface", "Re-create Symlink?"),
                    self.translate(
                        "SteamcmdInterface",
                        "An existing symlink already exists."
                        " Would you like to delete and re-create the symlink?",
                    ),
                    self.translate(
                        "SteamcmdInterface",
                        "The symlink makes SteamCMD download mods to the local mods folder"
                        + " and is required for SteamCMD mod downloads to work correctly.",
                    ),
                    self.translate(
                        "SteamcmdInterface",
                        "Existing symlink: {symlink_destination_path}"
                        "\n\nNew symlink:"
                        "\n[{symlink_source_path}] -> ",
                    ).format(
                        symlink_source_path=symlink_source_path,
                        symlink_destination_path=symlink_destination_path,
                    )
                    + symlink_destination_path,
                )
                if answer == QMessageBox.StandardButton.Yes:  # Re-create symlink
                    self.setup = self.create_symlink(
                        symlink_source_path, symlink_destination_path, runner=runner
                    )
            elif os.path.exists(
                symlink_destination_path
            ):  # A dir exists (not a symlink/junction)
                runner.message(
                    f"Symlink destination already exists! Please remove existing destination:\n\n{symlink_destination_path}\n"
                )
                answer = show_dialogue_conditional(
                    self.translate("SteamcmdInterface", "Re-create Symlink?"),
                    self.translate(
                        "SteamcmdInterface",
                        "The symlink destination path already exists."
                        " Would you like to remove the existing destination and create a new symlink in it's place?",
                    ),
                    self.translate(
                        "SteamcmdInterface",
                        "The symlink makes SteamCMD download mods to the local mods folder"
                        + " and is required for SteamCMD mod downloads to work correctly.",
                    ),
                    self.translate(
                        "SteamcmdInterface",
                        "Existing destination: {symlink_destination_path}"
                        "\n\nNew symlink:"
                        "\n[{symlink_source_path}] -> ",
                    ).format(
                        symlink_source_path=symlink_source_path,
                        symlink_destination_path=symlink_destination_path,
                    )
                    + symlink_destination_path,
                )
                if (
                    answer == QMessageBox.StandardButton.Yes
                ):  # Re-create symlink/junction
                    self.setup = self.create_symlink(
                        symlink_source_path, symlink_destination_path, runner=runner
                    )
            else:  # Symlink/junction does not exist
                answer = show_dialogue_conditional(
                    self.translate("SteamcmdInterface", "Create Symlink?"),
                    self.translate(
                        "SteamcmdInterface", "Do you want to create a symlink?"
                    ),
                    self.translate(
                        "SteamcmdInterface",
                        "The symlink makes SteamCMD download mods to the local mods folder"
                        + " and is required for SteamCMD mod downloads to work correctly.",
                    ),
                    self.translate(
                        "SteamcmdInterface", "New symlink:\n[{symlink_source_path}] -> "
                    ).format(
                        symlink_source_path=symlink_source_path,
                    )
                    + symlink_destination_path,
                )
                if answer == QMessageBox.StandardButton.Yes:
                    self.setup = self.create_symlink(
                        symlink_source_path, symlink_destination_path, runner=runner
                    )


if __name__ == "__main__":
    sys.exit()
