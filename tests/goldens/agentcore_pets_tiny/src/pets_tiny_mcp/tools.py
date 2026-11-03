"""MCP tool definitions derived from OpenAPI IR."""

from __future__ import annotations

TOOLS = {
  "listpets": {
    "description": "List all pets",
    "inputSchema": {
      "type": "object",
      "properties": {
        "limit": {
          "type": "integer",
          "description": "Maximum number of pets to return",
          "maximum": 100
        }
      }
    },
    "upstream": {
      "method": "GET",
      "path": "/pets"
    },
    "operationId": "listPets"
  },
  "showpetbyid": {
    "description": "Info for a specific pet",
    "inputSchema": {
      "type": "object",
      "properties": {
        "petId": {
          "type": "string"
        }
      },
      "required": [
        "petId"
      ]
    },
    "upstream": {
      "method": "GET",
      "path": "/pets/{petId}"
    },
    "operationId": "showPetById"
  }
}
