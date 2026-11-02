"""Shared sizing and dialog-layout helpers."""
from PyQt6.QtWidgets import QApplication

def fit_dialog_to_screen(dialog, parent, preferred_width: int, preferred_height: int) -> None:
    """Keep large study dialogs roomy without letting them spill off smaller displays."""
    screen=parent.screen() if parent is not None else QApplication.primaryScreen()
    if screen is None:return
    area=screen.availableGeometry()
    dialog.resize(min(preferred_width,max(480,int(area.width()*.94))),
                  min(preferred_height,max(420,int(area.height()*.92))))

__all__ = ["fit_dialog_to_screen"]
