"""Audio playback utilities."""

import logging
import os
import shutil
import sys
import threading
import time
from multiprocessing import Process

import psutil

from utils.path_utils import get_user_data_root

logger = logging.getLogger(__name__)


def is_audio_playback_available() -> bool:
    """Return whether the bundled audio backend is available on this platform."""
    return not sys.platform.startswith("linux") or shutil.which("gst-play-1.0") is not None


def _parent_process_is_alive(parent_pid: int) -> bool:
    """Return whether the application process that started playback still exists."""
    try:
        parent = psutil.Process(parent_pid)
        return parent.is_running() and parent.status() != psutil.STATUS_ZOMBIE
    except (psutil.Error, OSError):
        return False


def _watch_parent_process(parent_pid: int) -> None:
    """Stop an audio worker left behind after an unexpected application exit."""
    while _parent_process_is_alive(parent_pid):
        time.sleep(0.5)
    os._exit(0)


def _play_sound_process(sound_path: str, parent_pid: int | None = None) -> None:
    if parent_pid is not None:
        threading.Thread(
            target=_watch_parent_process,
            args=(parent_pid,),
            daemon=True,
            name="g3m-audio-parent-watch",
        ).start()
    try:
        from playsound3 import playsound

        playsound(os.path.abspath(sound_path))
    except Exception as error:
        logger.debug("Audio playback failed for %s: %s", sound_path, error)


class AudioManager:
    """Manages audio playback for the application."""

    def __init__(self) -> None:
        self._sound_instance: Process | None = None

    def play_g3m_sound(self) -> None:
        app_support_path = os.path.join(get_user_data_root(), "settings")
        asset_wav = os.path.join(
            os.path.dirname(__file__), "..", "..", "assets", "audio", "G3M.wav"
        )
        extensions = (".mp3", ".wav", ".ogg", ".flac", ".m4a", ".aac")
        candidates = [
            os.path.join(app_support_path, f"custom_startup_sound{ext}")
            for ext in extensions
        ]
        candidates.append(asset_wav)
        sound_path = None
        for candidate in candidates:
            if os.path.exists(candidate):
                sound_path = candidate
                break
        if not sound_path:
            return
        if not is_audio_playback_available():
            logger.debug("Startup sound disabled: gst-play-1.0 is unavailable")
            return
        try:
            self.stop_g3m_sound()
            process = Process(
                target=_play_sound_process,
                args=(os.path.abspath(sound_path), os.getpid()),
                daemon=True,
            )
            process.start()
            self._sound_instance = process
        except Exception as e:
            logger.debug(
                f"AudioManager.play_g3m_sound: failed to play {sound_path}: {e}"
            )
            self._sound_instance = None

    def stop_g3m_sound(self) -> None:
        process = self._sound_instance
        if process is not None:
            try:
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=1.0)
                    if process.is_alive():
                        process.kill()
                        process.join(timeout=0.5)
            except Exception as e:
                logger.debug(f"AudioManager.stop_g3m_sound: failed to stop: {e}")
        self._sound_instance = None


_audio_service = AudioManager()
