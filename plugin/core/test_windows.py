from .windows import WindowManager, WindowRegistry, WindowLike, ViewLike
from .sessions import create_session, Session
from .test_session import TestClient, test_config
from .test_rpc import TestSettings
from .events import global_events
# from .test_sublime import *
from . import test_sublime as test_sublime
import os
import unittest

try:
    from typing import Callable, List, Optional, Set, Dict
    assert Callable and List and Optional and Set and Session and Dict
except ImportError:
    pass


class TestSublimeSettings(object):
    def __init__(self, values):
        self._values = values

    def get(self, key):
        return self._values.get(key)

    def set(self, key, value):
        self._values[key] = value


class TestView(object):
    def __init__(self, file_name):
        self._file_name = file_name
        self._window = None
        self._settings = TestSublimeSettings({"syntax": "Plain Text"})
        self._status = dict()  # type: Dict[str, str]
        self._text = "asdf"

    def file_name(self):
        return self._file_name

    def set_window(self, window):
        self._window = window

    def set_status(self, key, status):
        self._status[key] = status

    def window(self):
        return self._window

    def settings(self):
        return self._settings

    def substr(self, region):
        return self._text

    def size(self):
        return len(self._text)

    def buffer_id(self):
        return 1


class TestHandlerDispatcher(object):
    def __init__(self, can_start: bool = True) -> None:
        self._can_start = can_start
        self._initialized = set()  # type: Set[str]

    def on_start(self, config_name: str) -> bool:
        return self._can_start

    def on_initialized(self, config_name: str, client):
        self._initialized.add(config_name)


class TestWindow(object):
    def __init__(self, files_in_groups: 'List[List[ViewLike]]' = []) -> None:
        self._files_in_groups = files_in_groups
        self._is_valid = True
        self._folders = [os.path.dirname(__file__)]

    def id(self):
        return 0

    def folders(self):
        return self._folders

    def set_folders(self, folders):
        self._folders = folders

    def num_groups(self):
        return len(self._files_in_groups)

    def active_group(self):
        return 0

    def project_data(self) -> Optional[dict]:
        return None

    def active_view(self) -> Optional[ViewLike]:
        return self.active_view_in_group(0)

    def close(self):
        self._is_valid = False

    def is_valid(self):
        return self._is_valid

    def active_view_in_group(self, group):
        if group < len(self._files_in_groups):
            files = self._files_in_groups[group]
            if len(files) > 0:
                return files[0]
            else:
                return TestView(None)

    def status_message(self, msg: str) -> None:
        pass


class TestGlobalConfigs(object):
    def for_window(self, window):
        return TestConfigs()


class TestConfigs(object):
    def is_supported(self, view):
        return self.scope_config(view) is not None

    def scope_config(self, view):
        if view.file_name() is None:
            return None
        else:
            return test_config


class TestDocuments(object):
    def __init__(self):
        self._documents = []  # type: List[str]

    def add_session(self, session: 'Session') -> None:
        pass

    def remove_session(self, config_name: str) -> None:
        pass

    def handle_view_opened(self, view: ViewLike):
        file_name = view.file_name()
        if file_name:
            self._documents.append(file_name)

    def reset(self, window):
        self._documents = []


class TestDocumentHandlerFactory(object):
    def for_window(self, window):
        return TestDocuments()


class TestDiagnostics(object):
    def __init__(self):
        pass

    def update(self, window: WindowLike, client_name: str, update: dict) -> None:
        pass

    def remove(self, view: ViewLike, client_name: str) -> None:
        pass


def test_start_session(window, project_path, config, on_created: 'Callable', on_ended: 'Callable'):
    return create_session(test_config, project_path, dict(), TestSettings(),
                          bootstrap_client=TestClient(),
                          on_created=on_created,
                          on_ended=on_ended)


class WindowRegistryTests(unittest.TestCase):

    def test_can_get_window_state(self):
        windows = WindowRegistry(TestGlobalConfigs(), TestDocumentHandlerFactory(),
                                 TestDiagnostics(), test_start_session,
                                 test_sublime, TestHandlerDispatcher())
        test_window = TestWindow()
        wm = windows.lookup(test_window)
        self.assertIsNotNone(wm)

    def test_removes_window_state(self):
        global_events.reset()
        test_window = TestWindow([[TestView(__file__)]])
        windows = WindowRegistry(TestGlobalConfigs(), TestDocumentHandlerFactory(),
                                 TestDiagnostics(), test_start_session,
                                 test_sublime, TestHandlerDispatcher())
        wm = windows.lookup(test_window)
        wm.start_active_views()

        self.assertIsNotNone(wm)

        # closing views triggers window unload detection
        test_window.close()
        global_events.publish("view.on_close", TestView(__file__))

        self.assertEqual(len(windows._windows), 0)


class WindowManagerTests(unittest.TestCase):

    def test_can_start_active_views(self):
        docs = TestDocuments()
        wm = WindowManager(TestWindow([[TestView(__file__)]]), TestConfigs(), docs,
                           TestDiagnostics(), test_start_session, test_sublime, TestHandlerDispatcher())
        wm.start_active_views()

        # session must be started (todo: verify session is ready)
        self.assertIsNotNone(wm.get_session(test_config.name))

        #
        self.assertListEqual(docs._documents, [__file__])

    def test_can_open_supported_view(self):
        docs = TestDocuments()
        window = TestWindow([[]])
        wm = WindowManager(window, TestConfigs(), docs, TestDiagnostics(), test_start_session, test_sublime,
                           TestHandlerDispatcher())

        wm.start_active_views()
        self.assertIsNone(wm.get_session(test_config.name))
        self.assertListEqual(docs._documents, [])

        # session must be started (todo: verify session is ready)
        wm.activate_view(TestView(__file__))
        self.assertIsNotNone(wm.get_session(test_config.name))
        self.assertListEqual(docs._documents, [__file__])

    def test_can_restart_sessions(self):
        docs = TestDocuments()
        wm = WindowManager(TestWindow([[TestView(__file__)]]), TestConfigs(), docs,
                           TestDiagnostics(), test_start_session, test_sublime, TestHandlerDispatcher())
        wm.start_active_views()

        # session must be started (todo: verify session is ready)
        self.assertIsNotNone(wm.get_session(test_config.name))

        # our starting document must be loaded
        self.assertListEqual(docs._documents, [__file__])

        wm.restart_sessions()

        # session must be started (todo: verify session is ready)
        self.assertIsNotNone(wm.get_session(test_config.name))

        # our starting document must be loaded
        self.assertListEqual(docs._documents, [__file__])

    def test_ends_sessions_when_closed(self):
        global_events.reset()
        docs = TestDocuments()
        test_window = TestWindow([[TestView(__file__)]])
        wm = WindowManager(test_window, TestConfigs(), docs,
                           TestDiagnostics(), test_start_session, test_sublime, TestHandlerDispatcher())
        wm.start_active_views()

        # session must be started (todo: verify session is ready)
        self.assertIsNotNone(wm.get_session(test_config.name))

        # our starting document must be loaded
        self.assertListEqual(docs._documents, [__file__])

        # closing views triggers window unload detection
        test_window.close()
        global_events.publish("view.on_close", TestView(__file__))

        self.assertEqual(len(wm._sessions), 0)

    def test_ends_sessions_when_quick_switching(self):
        global_events.reset()
        docs = TestDocuments()
        test_window = TestWindow([[TestView(__file__)]])
        wm = WindowManager(test_window, TestConfigs(), docs,
                           TestDiagnostics(), test_start_session, test_sublime, TestHandlerDispatcher())
        wm.start_active_views()

        # session must be started (todo: verify session is ready)
        self.assertIsNotNone(wm.get_session(test_config.name))

        # our starting document must be loaded
        self.assertListEqual(docs._documents, [__file__])

        # change project_path
        test_window.set_folders([os.path.dirname(__file__) + '/'])
        # global_events.publish("view.on_close", TestView(__file__))
        wm.activate_view(TestView(None))

        self.assertEqual(len(wm._sessions), 0)

    def test_offers_restart_on_crash(self):
        docs = TestDocuments()
        wm = WindowManager(TestWindow([[TestView(__file__)]]), TestConfigs(), docs,
                           TestDiagnostics(), test_start_session, test_sublime,
                           TestHandlerDispatcher())
        wm.start_active_views()

        # session must be started (todo: verify session is ready)
        self.assertIsNotNone(wm.get_session(test_config.name))

        # our starting document must be loaded
        self.assertListEqual(docs._documents, [__file__])

        wm._handle_server_crash(test_config)

        # session must be started (todo: verify session is ready)
        self.assertIsNotNone(wm.get_session(test_config.name))

        # our starting document must be loaded
        self.assertListEqual(docs._documents, [__file__])

    def test_invokes_language_handler(self):
        docs = TestDocuments()
        dispatcher = TestHandlerDispatcher()
        wm = WindowManager(TestWindow([[TestView(__file__)]]), TestConfigs(), docs,
                           TestDiagnostics(), test_start_session, test_sublime,
                           dispatcher)
        wm.start_active_views()

        # session must be started (todo: verify session is ready)
        self.assertIsNotNone(wm.get_session(test_config.name))

        # our starting document must be loaded
        self.assertListEqual(docs._documents, [__file__])

        # client_start_listeners, client_initialization_listeners,
        self.assertTrue(test_config.name in dispatcher._initialized)
