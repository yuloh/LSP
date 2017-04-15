import sublime_plugin
import sublime
import subprocess
import threading
import json
import sys
import urllib.request as urllib
from urllib.parse import urljoin
import html

PLUGIN_NAME = 'LSP'
SUBLIME_WORD_MASK = 515

server_binary_path = "javascript-typescript-stdio"
supported_scope = 'source.ts'
supported_syntaxes = ['Packages/TypeScript-TmLanguage/TypeScript.tmLanguage']
autocomplete_triggers = []
signature_help_triggers = []
is_hover_available = False
is_references_available = False
is_definition_available = False
is_rename_available = False


def format_request(request):
    """Converts the request into json and adds the Content-Length header"""
    content = json.dumps(request, indent=2)
    content_length = len(content)

    result = "Content-Length: {}\r\n\r\n{}".format(content_length, content)
    return result


class Client(object):

    def __init__(self, process):
        self.process = process
        self.stdout_thread = threading.Thread(target=self.read_stdout)
        self.stdout_thread.start()
        self.stderr_thread = threading.Thread(target=self.read_stderr)
        self.stderr_thread.start()
        self.request_id = 0
        self.handlers = {}

    def send_request(self, request, handler):
        self.request_id += 1
        request.id = self.request_id
        if handler is not None:
            self.handlers[request.id] = handler
        self.send_call(request)

    def send_notification(self, notification):
        self.send_call(notification)

    def send_call(self, payload):
        try:
            debug(payload)
            message = format_request(payload.__dict__)
            self.process.stdin.write(bytes(message, 'UTF-8'))
            self.process.stdin.flush()
        except BrokenPipeError as e:
            printf("client unexpectedly died:", e)

    def read_stdout(self):
        """
        Reads JSON responses from process and dispatch them to response_handler
        """
        ContentLengthHeader = b"Content-Length: "

        while self.process.poll() is None:
            try:

                in_headers = True
                content_length = 0

                while in_headers:
                    header = self.process.stdout.readline().strip()
                    if (len(header) == 0):
                        in_headers = False

                    if (header.startswith(ContentLengthHeader)):
                        content_length = int(header[len(ContentLengthHeader):])

                if (content_length > 0):
                    content = self.process.stdout.read(content_length).decode("UTF-8")
                    # debug(content)

                    response = None
                    try:
                        response = json.loads(content)
                        # debug("got json: ", response)
                    except:
                        printf("Got a non-JSON response: ", content)
                        continue

                    try:
                        if "error" in response:
                            debug("got error: ", response.get("error"))
                        elif "id" in response:
                            self.response_handler(response)
                        elif "method" in response:
                            self.notification_handler(response)
                        else:
                            debug("Unknown response type: ", response)
                    except Exception as err:
                        printf("Error handling server content:", err)

            except:
                printf("LSP stdout process ending due to exception: ", sys.exc_info())
                self.process.terminate()
                self.process = None
                return

        debug("LSP stdout process ended.")

    def read_stderr(self):
        """
        Reads any errors from the LSP process.
        """
        while self.process.poll() is None:
            try:
                error = self.process.stderr.readline().decode('UTF-8')
                if len(error) > 0:
                    printf("LSP error: ", error)
            except:
                printf("LSP stderr process ending due to exception: ", sys.exc_info())
                return

        debug("LSP stderr process ended.")

    def response_handler(self, response):
        # todo: try catch ?
        if (self.handlers[response.get("id")]):
            self.handlers[response.get("id")](response.get("result"))
        else:
            debug("No handler found for id" + response.get("id"))

    def notification_handler(self, response):
        method = response.get("method")
        if method == "textDocument/publishDiagnostics":
            Events.publish("document.diagnostics", response.get("params"))
        elif method == "window/showMessage":
            sublime.active_window().message_dialog(response.get("params").get("message"))
        elif method == "window/logMessage":
            server_log(response.get("params").get("message"))
        else:
            debug("Unhandled notification:", method)


def debug(*args):
    """Print args to the console if the "debug" setting is True."""
    # if settings.get('debug'):
    printf(*args)

def server_log(*args):
    print(server_binary_path + ": ", end='')

    for arg in args:
        print(arg, end=' ')

    print()


def printf(*args):
    """Print args to the console, prefixed by the plugin name."""
    print(PLUGIN_NAME + ': ', end='')

    for arg in args:
        print(arg, end=' ')

    print()


def first_folder(window):
    """
    We only support running one stack-ide instance per window currently,
    on the first folder open in that window.
    """
    if len(window.folders()):
        return window.folders()[0]
    else:
        debug("Couldn't find a folder for stack-ide-sublime")
        return None


def plugin_loaded():
    # TODO: unsubscribe once plugin is loaded?
    Events.subscribe("view.on_load_async", initialize_on_open)
    Events.subscribe("view.on_activated_async", initialize_on_open)
    debug("plugin loaded")


def plugin_unloaded():
    if client is not None:
        client.send_notification(Notification.exit())
    debug("plugin unloaded")


def is_supported_view(view):
    return view.match_selector(view.sel()[0].begin(), supported_scope)


client = None


TextDocumentSyncKindNone = 0
TextDocumentSyncKindFull = 1
TextDocumentSyncKindIncremental = 2

didopen_after_initialize = list()


def filename_to_uri(path):
    return urljoin('file:', urllib.pathname2url(path))


def uri_to_filename(uri):
    return urllib.url2pathname(uri).replace("file://", "")


def initialize_on_open(view):
    global client, didopen_after_initialize
    if is_supported_view(view) and client is None:
        didopen_after_initialize.append(view)
        get_client(view)


def notify_did_open(view):
    global client
    params = {
        "textDocument": {
           "uri": filename_to_uri(view.file_name()),
           "languageId": "ts",
           # "version": 0,
           "text": view.substr(sublime.Region(0, view.size()))

        }
    }
    client.send_notification(Notification.didOpen(params))


def notify_did_close(view):
    global client
    params = {
        "textDocument": {
            "uri": filename_to_uri(view.file_name())
        }
    }
    client.send_notification(Notification.didClose(params))


def notify_did_save(view):
    global client
    params = {
        "textDocument": {
            "uri": filename_to_uri(view.file_name())
        }
    }
    client.send_notification(Notification.didSave(params))


documentVersion = 0
document_states = {}


class DocumentState:
    def __init__(self, path):
        self.path = path
        self.version = 0

    def inc_version(self):
        self.version += 1
        return self.version


def get_document_state(path):
    if path not in document_states:
        document_states[path] = DocumentState(path)
    return document_states.get(path)


def notify_did_change(view):
    global client
    document_state = get_document_state(view.file_name())
    params = {
        "textDocument": {
           "uri": filename_to_uri(view.file_name()),
           "languageId": "ts",
           "version": document_state.inc_version(),
        },
        "contentChanges": [{
            "text": view.substr(sublime.Region(0, view.size()))
        }]
    }
    client.send_notification(Notification.didChange(params))


def initialize_document_sync(text_document_sync_kind):
    Events.subscribe('view.on_load_async', notify_did_open)
    Events.subscribe('view.on_modified_async', notify_did_change)
    Events.subscribe('view.on_post_save_async', notify_did_save)
    Events.subscribe('view.on_close', notify_did_close)


def initialize_document_completion(completion_capabilities):
    triggers = completion_capabilities.get("triggerCharacters")
    autocomplete_triggers.extend(triggers)


def initialize_signature_help(signature_help_capabilities):
    triggers = signature_help_capabilities.get("triggerCharacters")
    signature_help_triggers.extend(triggers)


def initialize_references():
    global is_references_available
    is_references_available = True


def initialize_hover():
    global is_hover_available
    is_hover_available = True


def initialize_definition():
    global is_definition_available
    is_definition_available = True


def initialize_rename():
    global is_rename_available
    is_rename_available = True


def handle_initialize_result(result):
    global didopen_after_initialize
    capabilities = result.get("capabilities")

    document_sync = capabilities.get("textDocumentSync")
    if document_sync:
        initialize_document_sync(document_sync)

    completion_provider = capabilities.get("completionProvider")
    if completion_provider:
        initialize_document_completion(completion_provider)

    signature_help_provider = capabilities.get("signatureHelpProvider")
    if signature_help_provider:
        initialize_signature_help(signature_help_provider)

    hover_provider = capabilities.get("hoverProvider")
    if hover_provider:
        initialize_hover()

    references_provider = capabilities.get("referencesProvider")
    if references_provider:
        initialize_references()

    definition_provider = capabilities.get("definitionProvider")
    if definition_provider:
        initialize_definition()

    Events.subscribe('document.diagnostics', handle_diagnostics)
    for view in didopen_after_initialize:
        notify_did_open(view)
    didopen_after_initialize = list()


phantomset = None

stylesheet = '''
            <style>
                div.error {
                    padding: 0.4rem 0 0.4rem 0.7rem;
                    margin: 0.2rem 0;
                    border-radius: 2px;
                }
                div.error span.message {
                    padding-right: 0.7rem;
                }
                div.error a {
                    text-decoration: inherit;
                    padding: 0.35rem 0.7rem 0.45rem 0.8rem;
                    position: relative;
                    bottom: 0.05rem;
                    border-radius: 0 2px 2px 0;
                    font-weight: bold;
                }
                html.dark div.error a {
                    background-color: #00000018;
                }
                html.light div.error a {
                    background-color: #ffffff18;
                }
            </style>
        '''


def create_phantom_html(text):
    global stylesheet
    return """<body id=inline-error>{}
                <div class="error">
                    <span class="message">{}</span>
                    <a href=hide>{}</a>
                </div>
                </body>""".format(stylesheet, html.escape(text, quote=False), chr(0x00D7))


def create_phantom(view, diagnostic):
    region = create_region(view, diagnostic)
    # TODO: hook up hide phantom (if keeping them)
    return sublime.Phantom(region, '<p>' + create_phantom_html(diagnostic.get('message')) + '</p>', sublime.LAYOUT_BELOW)


def create_region(view, diagnostic):
    start = diagnostic.get('range').get('start')
    end = diagnostic.get('range').get('end')
    region = sublime.Region(view.text_point(start.get('line'), start.get('character')),
                            view.text_point(end.get('line'), end.get('character')))
    return region


def format_diagnostic(file_path, diagnostic):
    start = diagnostic.get('range').get('start')
    return "{}\t{}:{}\t{}".format(file_path, start.get('line'), start.get('character'), diagnostic.get('message'))

class SymbolRenameCommand(sublime_plugin.TextCommand):
    def is_enabled(self):
        global is_rename_available
        # TODO: check what kind of scope we're in.
        if is_rename_available and is_supported_view(self.view):
            point = self.view.sel()[0].begin()
            word_at_sel = self.view.classify(point)
            if word_at_sel & SUBLIME_WORD_MASK:
                return True
            else:
                return False
        else:
            return False

    def run(self, edit):
        pos = self.view.sel()[0].begin()
        params = get_document_position(self.view, pos)
        sublime.active_window().show_input_panel("New name:", "", lambda text: self.request_rename(params, text), None, None)

    def request_rename(self, params, new_name):
        params["newName"] = new_name
        client.send_request(Request.rename(params),
                            lambda response: self.handle_response(response, pos))

    def handle_response(self, response, pos):
        debug(response)


class SymbolDefinitionCommand(sublime_plugin.TextCommand):
    def is_enabled(self):
        global is_definition_available
        # TODO: check what kind of scope we're in.
        if is_definition_available and is_supported_view(self.view):
            point = self.view.sel()[0].begin()
            word_at_sel = self.view.classify(point)
            if word_at_sel & SUBLIME_WORD_MASK:
                return True
            else:
                return False
        else:
            return False

    def run(self, edit):
        pos = self.view.sel()[0].begin()
        client.send_request(Request.definition(get_document_position(self.view, pos)),
                            lambda response: self.handle_response(response, pos))

    def handle_response(self, response, position):
        window = sublime.active_window()
        if len(response) < 1:
                # view.set_status("diagnostics", "{} errors".format(len(diagnostics)))
            view.set_status("definition", "Could not find definition")
        else:
            location = response[0]
            file_path = uri_to_filename(location.get("uri"))
            start = location.get('range').get('start')
            file_location = "{}:{}:{}".format(file_path, start.get('line') + 1, start.get('character') + 1)
            debug("opening location", location)
            window.open_file(file_location, sublime.ENCODED_POSITION)
            # TODO: can add region here.


class SymbolReferencesCommand(sublime_plugin.TextCommand):
    def is_enabled(self):
        global is_references_available
        # TODO: check what kind of scope we're in.
        if is_references_available and is_supported_view(self.view):
            point = self.view.sel()[0].begin()
            word_at_sel = self.view.classify(point)
            if word_at_sel & SUBLIME_WORD_MASK:
                return True
            else:
                return False
        else:
            return False


    def run(self, edit):
        pos = self.view.sel()[0].begin()
        client.send_request(Request.references(get_document_position(self.view, pos)),
                            lambda response: self.handle_response(response, pos))

    def handle_response(self, response, pos):
        window = sublime.active_window()
        references = list(format_reference(item) for item in response)

        if (len(response)) > 0:
            panel = window.find_output_panel("references")
            if panel is None:
                debug("creating panel")
                panel = window.create_output_panel("references")
                panel.settings().set("result_file_regex", r"^(.*)\t([0-9]+):?([0-9]+)$")

            panel.run_command("clear_error_panel")

            window.run_command("show_panel", {"panel": "output.references"})
            for reference in references:
                panel.run_command('append', {'characters': reference + "\n", 'force': True, 'scroll_to_end': True})

        else:
            window.run_command("hide_panel", {"panel": "output.references"})


def format_reference(reference):
    start = reference.get('range').get('start')
    file_path = uri_to_filename(reference.get("uri"))
    return "{}\t{}:{}".format(file_path, start.get('line'), start.get('character'))


class ClearErrorPanelCommand(sublime_plugin.TextCommand):
    """
    A clear_error_panel command to clear the error panel.
    """
    def run(self, edit):
        self.view.erase(edit, sublime.Region(0, self.view.size()))


UNDERLINE_FLAGS = sublime.DRAW_NO_FILL | sublime.DRAW_NO_OUTLINE | sublime.DRAW_EMPTY_AS_OVERWRITE


file_diagnostics = dict()

def handle_diagnostics(update):
    # TODO: should be per view?
    global phantomset
    file_path = uri_to_filename(update.get('uri'))
    window = sublime.active_window()

    diagnostics = update.get('diagnostics')
    phantoms = list()
    regions = list()

    view = window.find_open_file(file_path)
    if view is not None:
        if view.is_dirty():
            regions = list(create_region(view, diagnostic) for diagnostic in diagnostics)
        else:
            phantoms = list(create_phantom(view, diagnostic) for diagnostic in diagnostics)

        if phantomset is None:
            phantomset = sublime.PhantomSet(view, "diagnostics")

        phantomset.update(phantoms)

        if (len(regions)) > 0:
            # steal SublimeLinter's coloring.
            view.add_regions("errors", regions, "sublimelinter.mark.error", "dot", sublime.DRAW_SQUIGGLY_UNDERLINE | UNDERLINE_FLAGS)
        else:
            view.erase_regions("errors")

    output = list(format_diagnostic(file_path, diagnostic) for diagnostic in diagnostics)
    if output:
        file_diagnostics[file_path] = output
    else:
        if file_path in file_diagnostics:
            del file_diagnostics[file_path]

    update_output_panel(window)

def update_output_panel(window):
    panel = window.find_output_panel("diagnostics")
    if panel is None:
        panel = window.create_output_panel("diagnostics")
        panel.settings().set("result_file_regex", r"^(.*)\t([0-9]+):?([0-9]+)\t(.*)$")
    else:
        panel.run_command("clear_error_panel")
        window.run_command("show_panel", {"panel": "output.diagnostics"})

    if file_diagnostics:
        for file_path, diagnostics in file_diagnostics.items():
            if (len(diagnostics)) > 0:
                for message in diagnostics:
                    # exec.py just calls append command with extra params, check https://github.com/randy3k/sublime-default/blob/master/exec.py
                    # panel.run_command("append_to_error_panel", {"message": message})
                    panel.run_command('append', {'characters': message + "\n", 'force': True, 'scroll_to_end': True})
    else:
        window.run_command("hide_panel", {"panel": "output.diagnostics"})

    # view.set_status("diagnostics", "{} errors".format(len(diagnostics)))


def get_client(view):
    global client
    if client is None:
        client = start_server(server_binary_path)
        project_path = first_folder(view.window())
        initializeParams = {
            "processId": client.process.pid,
            "rootPath": project_path,
            "capabilities": {
                "completion": {
                    "completionItem": {
                        "snippetSupport": True
                    }
                }
            }
        }
        client.send_request(Request.initialize(initializeParams), handle_initialize_result)

    return client


def start_server(binary_path):
    args = [binary_path] #, "-t", "--logfile", "lspserver.log"]
    debug("starting " + str(args))
    try:
        process = subprocess.Popen(
            args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd="/Users/tomv/Library/Application Support/Sublime Text 3/Packages/LSP/")
        return Client(process)

    except Exception as err:
        print(err)


def get_document_position(view, point):
    if (point):
        (row, col) = view.rowcol(point)
    else:
        view.sel()
    return {
        "textDocument": {
            "uri": filename_to_uri(view.file_name())
        },
        "position": {
            "line": row,
            "character": col
        }
    }


class Request:
    def __init__(self, method, params):
        self.method = method
        self.params = params
        self.jsonrpc = "2.0"

    @classmethod
    def initialize(cls, params):
        return Request("initialize", params)

    @classmethod
    def hover(cls, params):
        return Request("textDocument/hover", params)

    @classmethod
    def complete(cls, params):
        return Request("textDocument/completion", params)

    @classmethod
    def signatureHelp(cls, params):
        return Request("textDocument/signatureHelp", params)

    @classmethod
    def references(cls, params):
        return Request("textDocument/references", params)

    @classmethod
    def definition(cls, params):
        return Request("textDocument/definition", params)

    @classmethod
    def rename(cls, params):
        return Request("textDocument/rename", params)

    def __repr__(self):
        return self.method + " " + str(self.params)


class Notification:
    def __init__(self, method, params):
        self.method = method
        self.params = params
        self.jsonrpc = "2.0"

    @classmethod
    def didOpen(cls, params):
        return Notification("textDocument/didOpen", params)

    @classmethod
    def didChange(cls, params):
        return Notification("textDocument/didChange", params)

    @classmethod
    def didSave(cls, params):
        return Notification("textDocument/didSave", params)

    @classmethod
    def didClose(cls, params):
        return Notification("textDocument/didClose", params)

    @classmethod
    def exit(cls):
        return Notification("exit", None)

    def __repr__(self):
        return self.method + " " + str(self.params)


class Events:
    listener_dict = dict()

    @classmethod
    def subscribe(cls, key, listener):
        if key in cls.listener_dict:
            cls.listener_dict[key].append(listener)
        else:
            cls.listener_dict[key] = [listener]

    @classmethod
    def publish(cls, key, *args):
        if key in cls.listener_dict:
            for listener in cls.listener_dict[key]:
                listener(*args)


class HoverHandler(sublime_plugin.ViewEventListener):
    def __init__(self, view):
        self.view = view

    @classmethod
    def is_applicable(cls, settings):
        syntax = settings.get('syntax')
        return syntax in supported_syntaxes

    def on_hover(self, point, hover_zone):
        global is_hover_available
        if is_hover_available and hover_zone == sublime.HOVER_TEXT:
            word_at_sel = self.view.classify(point)
            if word_at_sel & SUBLIME_WORD_MASK:
                client.send_request(Request.hover(get_document_position(self.view, point)),
                                    lambda response: self.handle_response(response, point))

    def handle_response(self, response, point):
        contents = response.get('contents')
        if len(contents) < 1:
            return
        html = '<h4>' + contents[0].get('value') + '</h4>'
        if len(contents) > 1:
            html += '<p>' + contents[1] + '</p>'
        self.view.show_popup(html, flags=sublime.HIDE_ON_MOUSE_MOVE_AWAY, location=point, max_width=800)


class CompletionHandler(sublime_plugin.EventListener):
    def __init__(self):
        self.completions = []
        self.refreshing = False

    def on_query_completions(self, view, prefix, locations):
        if not is_supported_view(view):
            return None

        if not self.refreshing:
            if locations[0] > 0:
                self.completions = []
                prev_char = view.substr(sublime.Region(locations[0] - 1, locations[0]))
                if prev_char not in autocomplete_triggers:
                    return None

            client.send_request(Request.complete(get_document_position(view, locations[0])), self.handle_response)

        self.refreshing = False
        return self.completions, sublime.INHIBIT_WORD_COMPLETIONS | sublime.INHIBIT_EXPLICIT_COMPLETIONS

    def format_completion(self, item):
        label = item.get("label")
        kind = item.get("kind")
        detail = item.get("detail")
        insertText = None
        if item.get("insertTextFormat") == 2:
            insertText = item.get("insertText")
        return ("{}\t{}".format(label, detail), insertText if insertText else label)

    def handle_response(self, response):
        items = response.get("items")
        self.completions = list(self.format_completion(item) for item in items)
        self.run_auto_complete()

    def run_auto_complete(self):
        self.refreshing = True
        sublime.active_window().active_view().run_command("auto_complete", {
            'disable_auto_insert': True,
            'api_completions_only': False,
            'next_completion_if_showing': False,
            'auto_complete_commit_on_tab': True,
        })


class SignatureHelpListener(sublime_plugin.ViewEventListener):
    def __init__(self, view):
        self.view = view

    @classmethod
    def is_applicable(cls, settings):
        syntax = settings.get('syntax')
        return syntax in supported_syntaxes

    def on_modified_async(self):
        pos = self.view.sel()[0].begin()
        last_char = self.view.substr(pos - 1)
        # TODO: this will fire too often, narrow down using scopes or regex
        if (last_char == '(') or last_char == ',':
            client.send_request(Request.signatureHelp(get_document_position(self.view, pos)),
                                lambda response: self.handle_response(response, pos))
        else:
            # TODO: this hides too soon.
            if self.view.is_popup_visible():
                self.view.hide_popup()

    def handle_response(self, response, point):
        signatures = response.get("signatures")
        if len(signatures) > 0:
            signature = signatures[response.get("activeSignature")]
            html = '<h4>' + signature.get('label') + '</h4>'
            html += '<p>' + signature.get('documentation') + '</p>'
            for parameter in signature.get('parameters'):
                paramDocs = parameter.get('documentation')
                html += '<p>' + parameter.get('label')
                if paramDocs:
                    html += ': ' + paramDocs
                html += '</p>'
            self.view.show_popup(html, flags=sublime.HIDE_ON_MOUSE_MOVE_AWAY, location=-1, max_width=800)


class SaveListener(sublime_plugin.EventListener):
    def on_post_save_async(self, view):
        if is_supported_view(view):
            # debug("on_post_save_async", view.file_name())
            Events.publish("view.on_post_save_async", view)


class DocumentSyncListener(sublime_plugin.ViewEventListener):
    def __init__(self, view):
        self.view = view

    @classmethod
    def is_applicable(cls, settings):
        syntax = settings.get('syntax')
        return syntax in supported_syntaxes

    @classmethod
    def applies_to_primary_view_only(cls):
        return False

    def on_load_async(self):
        # debug("on_load_async", self.view.file_name())
        Events.publish("view.on_load_async", self.view)

    def on_close(self):
        if self.view.file_name():
            # debug("on_close", self.view.file_name())
            #TODO check if more views are open for this file.
            Events.publish("view.on_close", self.view)

    def on_modified_async(self):
        if self.view.file_name():
            # debug("on_modified_async", self.view.file_name())
            Events.publish("view.on_modified_async", self.view)

    def on_activated_async(self):
        if self.view.file_name():
            # debug("on_activated_async", self.view.file_name())
            Events.publish("view.on_activated_async", self.view)
