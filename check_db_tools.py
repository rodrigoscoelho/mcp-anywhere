#!/usr/bin/env python3
"""Check tool names in database."""

import sqlite3

# Connect to the database
conn = sqlite3.connect('.data/mcp_anywhere.db')
cursor = conn.cursor()

# Query the tools for context7 server
cursor.execute("""
    SELECT id, server_id, tool_name
    FROM mcp_server_tools
    WHERE server_id = '6c335d0d'
""")

results = cursor.fetchall()
print("Tools in database for context7 server:")
for row in results:
    print(f"  ID: {row[0]}, Server ID: {row[1]}, Tool Name: {row[2]}")

conn.close()

