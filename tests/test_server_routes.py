"""The server's routes stay tidy (docs/server-routes.md): each exact path has
one handler in a themed class, the tables and the document agree, and
do_GET / do_POST keep only access, patterns and « Route introuvable »."""
import ast
import inspect
import re
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import app

ROOT = Path(__file__).resolve().parent.parent
THEMES = [cls for cls in app.RequestHandler.__mro__[1:] if cls.__module__ == "app" and cls.__name__.endswith("Routes")]


def handler(path, method="GET", body=None):
    request = object.__new__(app.RequestHandler)
    request.path = path
    request.headers = {}
    request._request_allowed = Mock(return_value=True)
    request.read_json_body = Mock(return_value=body or {})
    request.send_json = Mock()
    return request


class TableTests(unittest.TestCase):
    def test_seven_themes(self):
        self.assertEqual([cls.__name__ for cls in THEMES], ["PlaybackRoutes", "CatalogueRoutes", "LibraryRoutes", "DebridRoutes",
                                                            "ListsRoutes", "AccountRoutes", "AppRoutes"])

    def test_every_path_has_its_handler_in_a_theme(self):
        handled = set()
        for verb, table in (("get", app.RequestHandler.GET_ROUTES), ("post", app.RequestHandler.POST_ROUTES)):
            for path, name in table.items():
                self.assertTrue(path.startswith("/"), path)
                self.assertTrue(name.startswith(f"{verb}_"), name)
                owners = [cls for cls in THEMES if name in cls.__dict__]
                self.assertEqual(len(owners), 1, f"{name}: one theme")
                expected = ["self", "parsed", "params"] + (["body"] if verb == "post" else [])
                self.assertEqual(list(inspect.signature(getattr(app.RequestHandler, name)).parameters), expected, name)
                handled.add(name)
        defined = {name for cls in THEMES for name in cls.__dict__ if re.match(r"(get|post)_", name)}
        self.assertEqual(defined - handled, set(), "no handler without a path")

    def test_do_get_and_do_post_keep_no_exact_route(self):
        tree = ast.parse((ROOT / "src" / "app.py").read_text(encoding="utf-8"))
        request = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "RequestHandler")
        for method in (n for n in request.body if isinstance(n, ast.FunctionDef) and n.name in ("do_GET", "do_POST")):
            self.assertLess(method.end_lineno - method.lineno, 120, f"{method.name} stays short")
            for node in ast.walk(method):
                if isinstance(node, ast.If) and isinstance(node.test, ast.Compare) and len(node.test.ops) == 1 \
                        and isinstance(node.test.ops[0], ast.Eq) and isinstance(node.test.comparators[0], ast.Constant) \
                        and isinstance(node.test.left, ast.Attribute) and node.test.left.attr == "path" \
                        and isinstance(node.body[-1], ast.Return):
                    self.fail(f"{method.name}: {node.test.comparators[0].value} belongs in the route tables")

    def test_the_document_lists_every_route(self):
        document = (ROOT / "docs" / "dev" / "server-routes.md").read_text(encoding="utf-8")
        for verb, table in (("GET", app.RequestHandler.GET_ROUTES), ("POST", app.RequestHandler.POST_ROUTES)):
            for path, name in table.items():
                self.assertIn(f"| {verb} | `{path}` | `{name}` |", document)


class DispatchTests(unittest.TestCase):
    def test_a_path_reaches_its_handler_and_an_unknown_one_does_not(self):
        request = handler("/api/stats?x=1")
        with patch.object(app.RequestHandler, "get_stats") as stats:
            request.do_GET()
        parsed, params = stats.call_args.args
        self.assertEqual((parsed.path, params), ("/api/stats", {"x": "1"}))
        request = handler("/api/nothing")
        request.do_GET()
        request.send_json.assert_called_once_with({"error": "Route introuvable"}, status=404)

    def test_a_post_handler_gets_the_body_and_errors_stay_answers(self):
        request = handler("/api/watchlist", "POST", body={"id": "tt1"})
        with patch.object(app.RequestHandler, "post_watchlist") as watchlist:
            request.do_POST()
        self.assertEqual(watchlist.call_args.args[2], {"id": "tt1"})
        request = handler("/api/stats")
        with patch.object(app.RequestHandler, "get_stats", side_effect=RuntimeError("boom")):
            request.do_GET()
        request.send_json.assert_called_once_with({"error": "boom"}, status=500)


if __name__ == "__main__":
    unittest.main()
