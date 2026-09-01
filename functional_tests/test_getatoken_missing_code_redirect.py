# test_getatoken_missing_code_redirect.py
"""
Functional test for getAToken callback resilience.
Version: 0.261.007
Implemented in: 0.261.007

This test ensures that users who reach /getAToken directly are redirected to
the home sign-in page instead of seeing an authorization-code error, and that
OAuth callbacks use form POSTs so large authorization codes do not exceed
request-line limits.
"""

import ast
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
AUTH_ROUTE_PATH = ROOT_DIR / "application" / "single_app" / "route_frontend_authentication.py"
AUTH_FUNCTIONS_PATH = ROOT_DIR / "application" / "single_app" / "functions_authentication.py"


def _find_authorized_function(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "authorized":
            return node
    raise AssertionError("Could not find the /getAToken authorized route function.")


def _find_function(tree, function_name):
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == function_name:
            return node
    raise AssertionError(f"Could not find {function_name} function.")


def _route_decorator_for_path(function_node, route_path):
    for decorator in function_node.decorator_list:
        if not isinstance(decorator, ast.Call) or not decorator.args:
            continue
        first_arg = decorator.args[0]
        if isinstance(first_arg, ast.Constant) and first_arg.value == route_path:
            return decorator
    raise AssertionError(f"Could not find route decorator for {route_path}.")


def _route_methods(route_decorator):
    for keyword in route_decorator.keywords:
        if keyword.arg != "methods" or not isinstance(keyword.value, (ast.List, ast.Tuple)):
            continue
        return {
            element.value
            for element in keyword.value.elts
            if isinstance(element, ast.Constant)
        }
    return {"GET"}


def _is_callback_values_assignment(node):
    return (
        isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "callback_values" for target in node.targets)
        and isinstance(node.value, ast.Attribute)
        and isinstance(node.value.value, ast.Name)
        and node.value.value.id == "request"
        and node.value.attr == "values"
    )


def _reads_code_from_callback_values(node):
    return (
        isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "code" for target in node.targets)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Attribute)
        and isinstance(node.value.func.value, ast.Name)
        and node.value.func.value.id == "callback_values"
        and node.value.func.attr == "get"
        and node.value.args
        and isinstance(node.value.args[0], ast.Constant)
        and node.value.args[0].value == "code"
    )


def _has_response_mode_form_post(call_node):
    return any(
        keyword.arg == "response_mode"
        and isinstance(keyword.value, ast.Constant)
        and keyword.value.value == "form_post"
        for keyword in call_node.keywords
    )


def _calls_authorization_request_with_form_post(function_node):
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get_authorization_request_url"
        and _has_response_mode_form_post(node)
        for node in ast.walk(function_node)
    )


def _dict_assigns_response_mode_form_post(function_node):
    for node in ast.walk(function_node):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values):
            if (
                isinstance(key, ast.Constant)
                and key.value == "response_mode"
                and isinstance(value, ast.Constant)
                and value.value == "form_post"
            ):
                return True
    return False


def _is_missing_code_branch(node):
    return (
        isinstance(node, ast.If)
        and isinstance(node.test, ast.UnaryOp)
        and isinstance(node.test.op, ast.Not)
        and isinstance(node.test.operand, ast.Name)
        and node.test.operand.id == "code"
    )


def _returns_home_redirect(node):
    if not isinstance(node, ast.Return):
        return False
    value = node.value
    return (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Name)
        and value.func.id == "redirect"
        and len(value.args) == 1
        and isinstance(value.args[0], ast.Call)
        and isinstance(value.args[0].func, ast.Name)
        and value.args[0].func.id == "url_for"
        and len(value.args[0].args) == 1
        and isinstance(value.args[0].args[0], ast.Constant)
        and value.args[0].args[0].value == "public_app.index"
    )


def _returns_authorization_code_error(node):
    if not isinstance(node, ast.Return):
        return False
    value = node.value
    if isinstance(value, ast.Constant):
        return value.value == "Authorization code not found"
    if isinstance(value, ast.Tuple):
        return any(
            isinstance(element, ast.Constant)
            and element.value == "Authorization code not found"
            for element in value.elts
        )
    return False


def test_getatoken_missing_code_redirects_home():
    """Validate that /getAToken without a code redirects to the sign-in entry point."""
    print("Testing /getAToken missing authorization-code redirect...")

    tree = ast.parse(AUTH_ROUTE_PATH.read_text(encoding="utf-8"))
    authorized_function = _find_authorized_function(tree)
    missing_code_branches = [
        node for node in ast.walk(authorized_function) if _is_missing_code_branch(node)
    ]

    if len(missing_code_branches) != 1:
        raise AssertionError(f"Expected exactly one missing-code branch, found {len(missing_code_branches)}.")

    missing_code_branch = missing_code_branches[0]
    if not any(_returns_home_redirect(node) for node in missing_code_branch.body):
        raise AssertionError("Expected missing-code branch to redirect to public_app.index.")

    if any(_returns_authorization_code_error(node) for node in missing_code_branch.body):
        raise AssertionError("Missing-code branch must not return the authorization-code error to users.")

    print("/getAToken missing-code requests redirect to the sign-in entry point.")


def test_getatoken_accepts_form_post_callback():
    """Validate that the OAuth callback can receive posted authorization fields."""
    print("Testing /getAToken form-post callback support...")

    tree = ast.parse(AUTH_ROUTE_PATH.read_text(encoding="utf-8"))
    authorized_function = _find_authorized_function(tree)
    route_decorator = _route_decorator_for_path(authorized_function, "/getAToken")

    if _route_methods(route_decorator) != {"GET", "POST"}:
        raise AssertionError("Expected /getAToken to support both GET and POST callbacks.")

    if not any(_is_callback_values_assignment(node) for node in ast.walk(authorized_function)):
        raise AssertionError("Expected /getAToken callback to read request.values.")

    if not any(_reads_code_from_callback_values(node) for node in ast.walk(authorized_function)):
        raise AssertionError("Expected /getAToken callback to read code from request.values.")

    print("/getAToken accepts form-post callback fields.")


def test_auth_urls_request_form_post_response_mode():
    """Validate that interactive auth URLs avoid oversized query-string callbacks."""
    print("Testing OAuth authorization URLs request form_post response mode...")

    route_tree = ast.parse(AUTH_ROUTE_PATH.read_text(encoding="utf-8"))
    auth_functions_tree = ast.parse(AUTH_FUNCTIONS_PATH.read_text(encoding="utf-8"))

    login_function = _find_function(route_tree, "login")
    consent_url_function = _find_function(auth_functions_tree, "get_consent_url")

    if not _calls_authorization_request_with_form_post(login_function):
        raise AssertionError("Expected login auth URL generation to request response_mode='form_post'.")

    if not _dict_assigns_response_mode_form_post(consent_url_function):
        raise AssertionError("Expected plugin consent URL generation to request response_mode='form_post'.")

    print("OAuth authorization URLs request form_post response mode.")


if __name__ == "__main__":
    try:
        test_getatoken_missing_code_redirects_home()
        test_getatoken_accepts_form_post_callback()
        test_auth_urls_request_form_post_response_mode()
    except Exception as exc:
        print(f"Test failed: {exc}")
        sys.exit(1)

    print("All getAToken callback resilience tests passed")
