import json

import pytest
from fastapi.testclient import TestClient

from epistemic_virtue.web.app import app


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


def events(response):
    return [json.loads(line) for line in response.text.splitlines() if line]


def test_index_and_static(client):
    assert "Constitutional Guardian" in client.get("/").text
    assert client.get("/static/app.js").status_code == 200


def test_config(client):
    config = client.get("/api/config").json()
    assert [t["name"] for t in config["tasks"]] == [
        "calibration",
        "uncertainty",
        "manipulation",
        "claim_precision",
    ]
    assert config["tasks"][0]["items"] == {"seed": 3, "generated": 200}
    assert len(config["presets"]) == 5
    assert "Constitutional Guardian" in config["guardian_prompt"]


def test_review_endpoint(client):
    preset = client.get("/api/config").json()["presets"][1]
    body = {k: preset[k] for k in ("intent", "context", "draft")}
    result = client.post("/api/guardian/review", json=body).json()
    assert result["verdict"]["status"] == "FAIL"
    assert set(result["verdict"]) == {"chain_of_thought", "status", "revision_directives"}


def test_guarded_run_stream(client):
    body = {"intent": "What did the bridge cost?", "context": "The bridge is 2.4 km long."}
    stream = events(client.post("/api/guardian/run", json=body))
    assert stream[-1]["type"] == "final"
    assert stream[-1]["outcome"] == "approved"


def test_eval_stream_with_gate(client):
    body = {"task": "uncertainty", "guardian_model": "demo/guardian"}
    stream = events(client.post("/api/eval/run", json=body))
    assert [e["type"] for e in stream] == ["start", "item", "item", "item", "summary"]
    assert stream[-1]["baseline"] is not None


def test_eval_generated_item_set(client):
    body = {"task": "manipulation", "dataset": "generated", "limit": 8}
    stream = events(client.post("/api/eval/run", json=body))
    assert stream[0]["total"] == 8
    assert stream[1]["id"] == "man-gen-0001"
    # demo/actor has no script for generated items, so they are reported unscored.
    assert stream[-1]["unscored"] == 8


def test_validation_errors(client):
    no_generated = {"task": "uncertainty", "dataset": "generated"}
    assert client.post("/api/eval/run", json=no_generated).status_code == 400
    assert client.post("/api/eval/run", json={"task": "nope"}).status_code == 404
    assert client.post("/api/guardian/review", json={"intent": "", "draft": "x"}).status_code == 422
    bad_model = {"intent": "a", "draft": "b", "guardian_model": "demo/nope"}
    assert client.post("/api/guardian/review", json=bad_model).status_code == 400
    too_many = {"intent": "a", "max_revisions": 99}
    assert client.post("/api/guardian/run", json=too_many).status_code == 422
