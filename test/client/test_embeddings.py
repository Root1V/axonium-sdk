import pytest
from unittest.mock import Mock
from axonium.client.embeddings import Embeddings


class TestEmbeddings:
    def test_init(self):
        mock_client = Mock()
        embeddings = Embeddings(mock_client)
        assert embeddings._client == mock_client

    def test_create_single_input(self):
        mock_client = Mock()
        mock_response = {
            "object": "list",
            "data": [
                {
                    "object": "embedding",
                    "embedding": [0.1, 0.2, 0.3],
                    "index": 0
                }
            ],
            "model": "text-embedding-ada-002",
            "usage": {
                "prompt_tokens": 5,
                "total_tokens": 5
            }
        }
        mock_client._request.return_value = mock_response

        embeddings = Embeddings(mock_client)
        result = embeddings.create(
            model="text-embedding-ada-002",
            input=["Hello world"]
        )

        assert result == mock_response
        mock_client._request.assert_called_once_with(
            "POST",
            "/v1/embeddings",
            json={
                "model": "text-embedding-ada-002",
                "input": ["Hello world"]
            }
        )

    def test_create_multiple_inputs(self):
        mock_client = Mock()
        mock_response = {
            "object": "list",
            "data": [
                {
                    "object": "embedding",
                    "embedding": [0.1, 0.2, 0.3],
                    "index": 0
                },
                {
                    "object": "embedding",
                    "embedding": [0.4, 0.5, 0.6],
                    "index": 1
                }
            ],
            "model": "text-embedding-ada-002",
            "usage": {
                "prompt_tokens": 10,
                "total_tokens": 10
            }
        }
        mock_client._request.return_value = mock_response

        embeddings = Embeddings(mock_client)
        result = embeddings.create(
            model="text-embedding-ada-002",
            input=["First text", "Second text"]
        )

        assert result == mock_response
        assert len(result["data"]) == 2
        mock_client._request.assert_called_once_with(
            "POST",
            "/v1/embeddings",
            json={
                "model": "text-embedding-ada-002",
                "input": ["First text", "Second text"]
            }
        )

    def test_create_empty_input(self):
        mock_client = Mock()
        mock_response = {
            "object": "list",
            "data": [],
            "model": "text-embedding-ada-002",
            "usage": {
                "prompt_tokens": 0,
                "total_tokens": 0
            }
        }
        mock_client._request.return_value = mock_response

        embeddings = Embeddings(mock_client)
        result = embeddings.create(
            model="text-embedding-ada-002",
            input=[]
        )

        assert result == mock_response
        assert len(result["data"]) == 0

    def test_create_request_error(self):
        mock_client = Mock()
        mock_client._request.side_effect = Exception("Embedding API Error")

        embeddings = Embeddings(mock_client)
        with pytest.raises(Exception, match="Embedding API Error"):
            embeddings.create(
                model="text-embedding-ada-002",
                input=["Test text"]
            )