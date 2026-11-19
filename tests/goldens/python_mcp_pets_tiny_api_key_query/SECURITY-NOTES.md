# Security notes: Pets Tiny API MCP

The wizard found these risks in the operations and settings you chose.
They come from the upstream API's design: the generated server sends each value
where the API expects it.

## Authentication

- **API key in the query string.** Every request sends the API key as `api_key` in the URL, where proxies, servers and logs can record it. Prefer a header if the API accepts one (Security step: API key location = header), and keep the key out of shared logs.
