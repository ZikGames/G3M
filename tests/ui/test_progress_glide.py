"""The progress bar glides forward instead of jumping."""

from PyQt6.QtWidgets import QProgressBar

from ui.utils.ui_utils import UIAnimator


def _bar(qtbot, value=0, visible=True):
    bar = QProgressBar()
    qtbot.addWidget(bar)
    bar.setRange(0, 100)
    bar.setValue(value)
    if visible:
        bar.show()
    return bar


def test_a_visible_bar_glides_to_a_higher_value(qtbot):
    bar = _bar(qtbot)

    UIAnimator.progress_to(bar, 80, None, duration=120)

    assert bar.value() < 80
    qtbot.waitUntil(lambda: bar.value() == 80, timeout=2000)


def test_a_reset_is_immediate(qtbot):
    bar = _bar(qtbot, value=70)

    UIAnimator.progress_to(bar, 0)

    assert bar.value() == 0


def test_a_hidden_bar_is_set_directly(qtbot):
    bar = _bar(qtbot, visible=False)

    UIAnimator.progress_to(bar, 55)

    assert bar.value() == 55


def test_disabled_animations_set_the_value_directly(qtbot):
    bar = _bar(qtbot)
    state = type("State", (), {"local_config": {"disable_animations": True}})()

    UIAnimator.progress_to(bar, 60, state)

    assert bar.value() == 60


def test_a_newer_value_takes_over_a_running_glide(qtbot):
    bar = _bar(qtbot)
    UIAnimator.progress_to(bar, 90, None, duration=400)

    UIAnimator.progress_to(bar, 100, None, duration=60)

    qtbot.waitUntil(lambda: bar.value() == 100, timeout=2000)
    qtbot.wait(450)
    assert bar.value() == 100
