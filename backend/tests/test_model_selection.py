def test_chat_route_uses_selected_model_and_provider(client, monkeypatch):
    class RecordingLLMService:
        def __init__(self):
            self.last_call_kwargs = None

        def invoke(self, prompt, system_prompt=None):
            return "OK"

        def generate_json(self, prompt, system_prompt=None):
            return {"intent": "chat_only"}

    llm_service = RecordingLLMService()

    def fake_get_llm_service(*args, **kwargs):
        llm_service.last_call_kwargs = kwargs
        return llm_service

    monkeypatch.setattr("app.agent.nodes.get_llm_service", fake_get_llm_service)

    response = client.post(
        "/api/chat",
        json={
            "session_id": "session-1",
            "message": "hello",
            "llm_provider": "github",
            "model": "meta/Llama-3.3-70B-Instruct",
        },
    )

    assert response.status_code == 200
    assert llm_service.last_call_kwargs == {
        "provider": "github",
        "model": "meta/Llama-3.3-70B-Instruct",
    }
