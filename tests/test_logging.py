"""_setup_logging 的行为测试."""

import logging

import ocr_service


def test_setup_logging_writes_to_rotating_file(tmp_path):
    logfile = tmp_path / "svc.log"
    saved = list(logging.getLogger().handlers)
    try:
        ocr_service._setup_logging(level="info", log_file=str(logfile))
        logging.getLogger("ocr_service").info("hello-log")
        logging.getLogger("ocr_service").warning("warn-log")
        for h in logging.getLogger().handlers:
            h.flush()
            h.close()
        content = logfile.read_text(encoding="utf-8")
    finally:
        root = logging.getLogger()
        for h in list(root.handlers):
            root.removeHandler(h)
        for h in saved:
            root.addHandler(h)

    assert "hello-log" in content
    assert "warn-log" in content
    assert "ocr_service" in content  # logger name in format


def test_setup_logging_respects_level(tmp_path):
    logfile = tmp_path / "svc2.log"
    saved = list(logging.getLogger().handlers)
    try:
        ocr_service._setup_logging(level="error", log_file=str(logfile))
        logging.getLogger("ocr_service").info("should-not-appear")
        logging.getLogger("ocr_service").error("should-appear")
        for h in logging.getLogger().handlers:
            h.flush()
            h.close()
        content = logfile.read_text(encoding="utf-8")
    finally:
        root = logging.getLogger()
        for h in list(root.handlers):
            root.removeHandler(h)
        for h in saved:
            root.addHandler(h)

    assert "should-not-appear" not in content
    assert "should-appear" in content
