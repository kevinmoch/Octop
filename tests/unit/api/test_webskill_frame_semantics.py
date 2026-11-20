"""Nested frame discovery and routing identity for the P1 PageAgent."""

from __future__ import annotations

from octop.infra.control.browser.webskill.frames import (
    discover_frame_routes,
    nested_frame_refs,
    projection_document,
)
from octop.infra.control.browser.webskill.page_agent import _node_paths


def _nested_root() -> dict:
    return {
        "nodeName": "#document",
        "nodeId": 1,
        "children": [
            {
                "nodeName": "IFRAME",
                "nodeId": 2,
                "backendNodeId": 200,
                "attributes": ["id", "outer", "src", "/outer"],
                "contentDocument": {
                    "nodeName": "#document",
                    "nodeId": 3,
                    "children": [
                        {
                            "nodeName": "BUTTON",
                            "nodeId": 4,
                            "backendNodeId": 400,
                            "children": [{"nodeName": "#text", "nodeValue": "外层"}],
                        },
                        {
                            "nodeName": "IFRAME",
                            "nodeId": 5,
                            "backendNodeId": 500,
                            "attributes": ["id", "inner", "src", "/inner"],
                            "contentDocument": {
                                "nodeName": "#document",
                                "nodeId": 6,
                                "children": [
                                    {
                                        "nodeName": "BUTTON",
                                        "nodeId": 7,
                                        "backendNodeId": 700,
                                        "children": [{"nodeName": "#text", "nodeValue": "内层"}],
                                    }
                                ],
                            },
                        },
                    ],
                },
            }
        ],
    }


def test_discover_frame_routes_follows_nested_same_origin_documents() -> None:
    routes = discover_frame_routes(_nested_root(), "https://example.test/")
    assert [(route.path, route.url) for route in routes] == [
        (("iframe#outer",), "https://example.test/outer"),
        (("iframe#outer", "iframe#inner"), "https://example.test/inner"),
    ]


def test_nested_frame_refs_are_only_direct_children_of_the_selected_frame() -> None:
    routes = discover_frame_routes(_nested_root(), "https://example.test/")
    main = nested_frame_refs(routes, ())
    outer = nested_frame_refs(routes, ("iframe#outer",))
    assert [item["frame"] for item in main] == ["iframe#outer"]
    assert [item["frame"] for item in outer] == ["iframe#outer >>> iframe#inner"]


def test_projection_document_selects_the_nested_frame() -> None:
    routes = discover_frame_routes(_nested_root(), "https://example.test/")
    inner = routes[1]
    projected = projection_document(_nested_root(), inner)
    assert [child.get("nodeName") for child in projected["children"]] == ["BUTTON"]
    assert _node_paths({"nodeName": "#document", "nodeId": 1, "children": [projected]}) == {
        1: [],
        6: [0],
        7: [0, 0],
    }
