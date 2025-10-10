# MCP Server Testing Page - User Guide

## Overview

The MCP Server Testing Page allows you to interactively test and validate your configured MCP Server tools directly from the web interface, without needing external tools or clients.

## Accessing the Testing Page

1. Log in to MCP Anywhere
2. Click **"Test Tools"** in the top navigation menu
3. The testing page will load with your active servers

## Page Layout

The testing page is divided into two main sections:

### Left Panel: Server & Tool Selection
- **Server Selection**: Dropdown to choose which MCP server to test
- **Tool Selection**: List of available tools from the selected server

### Right Panel: Tool Form & Results
- **Tool Details**: Name and description of the selected tool
- **Parameter Form**: Dynamically generated input fields
- **Execution Results**: Real-time display of tool execution results

## Step-by-Step Usage

### Step 1: Select a Server

```
┌─────────────────────────────────┐
│ Select Server                   │
│ ┌─────────────────────────────┐ │
│ │ Choose a server...        ▼ │ │
│ └─────────────────────────────┘ │
│                                 │
│ Available servers:              │
│ • Weather API Server            │
│ • Database Tools                │
│ • File System Server            │
└─────────────────────────────────┘
```

Click the dropdown and select an active MCP server from the list.

### Step 2: Select a Tool

```
┌─────────────────────────────────┐
│ Select Tool                     │
│                                 │
│ ┌─────────────────────────────┐ │
│ │ get_weather                 │ │
│ │ Get current weather data    │ │
│ └─────────────────────────────┘ │
│                                 │
│ ┌─────────────────────────────┐ │
│ │ get_forecast                │ │
│ │ Get weather forecast        │ │
│ └─────────────────────────────┘ │
└─────────────────────────────────┘
```

Click on any tool from the list to load its parameter form.

### Step 3: Fill in Parameters

The form will automatically generate input fields based on the tool's schema:

#### Text Fields (String Parameters)
```
┌─────────────────────────────────┐
│ City Name *                     │
│ ┌─────────────────────────────┐ │
│ │ San Francisco               │ │
│ └─────────────────────────────┘ │
│ Enter the name of the city      │
└─────────────────────────────────┘
```

#### Number Fields (Integer/Number Parameters)
```
┌─────────────────────────────────┐
│ Temperature Threshold           │
│ ┌─────────────────────────────┐ │
│ │ 75                          │ │
│ └─────────────────────────────┘ │
│ Minimum: 0, Maximum: 100        │
└─────────────────────────────────┘
```

#### Boolean Fields (Checkbox)
```
┌─────────────────────────────────┐
│ Include Humidity                │
│ ☑ Enable this option            │
└─────────────────────────────────┘
```

#### Complex Fields (Arrays/Objects)
```
┌─────────────────────────────────┐
│ Coordinates                     │
│ ┌─────────────────────────────┐ │
│ │ {                           │ │
│ │   "lat": 37.7749,           │ │
│ │   "lon": -122.4194          │ │
│ │ }                           │ │
│ └─────────────────────────────┘ │
│ Enter valid JSON object         │
└─────────────────────────────────┘
```

**Note:** Fields marked with an asterisk (*) are required.

### Step 4: Execute the Tool

```
┌─────────────────────────────────┐
│ [Clear]          [Execute Tool] │
└─────────────────────────────────┘
```

Click the **"Execute Tool"** button to run the tool with your parameters.

### Step 5: View Results

#### Success Result
```
┌─────────────────────────────────────────────┐
│ Execution Results              [Clear]      │
├─────────────────────────────────────────────┤
│ Status: Success  Duration: 245ms            │
│ Time: 12/15/2024, 3:45:30 PM                │
├─────────────────────────────────────────────┤
│ Result                                      │
│ ┌─────────────────────────────────────────┐ │
│ │ {                                       │ │
│ │   "temperature": 72,                    │ │
│ │   "conditions": "Partly Cloudy",        │ │
│ │   "humidity": 65,                       │ │
│ │   "wind_speed": 12                      │ │
│ │ }                                       │ │
│ └─────────────────────────────────────────┘ │
└─────────────────────────────────────────────┘
```

#### Error Result
```
┌─────────────────────────────────────────────┐
│ Execution Results              [Clear]      │
├─────────────────────────────────────────────┤
│ Status: Error    Duration: 120ms            │
│ Time: 12/15/2024, 3:45:30 PM                │
├─────────────────────────────────────────────┤
│ Error                                       │
│ ┌─────────────────────────────────────────┐ │
│ │ Invalid city name: City not found       │ │
│ │ Code: CITY_NOT_FOUND                    │ │
│ └─────────────────────────────────────────┘ │
└─────────────────────────────────────────────┘
```

## Tips and Best Practices

### 1. Understanding Required Fields
- Required fields are marked with a red asterisk (*)
- The form will not submit if required fields are empty
- Hover over field labels for additional information

### 2. Working with JSON Fields
- For array parameters, use JSON array syntax: `["item1", "item2"]`
- For object parameters, use JSON object syntax: `{"key": "value"}`
- The system validates JSON before submission
- Invalid JSON will show an error message

### 3. Testing Strategies

#### Basic Validation
1. Test with valid inputs first
2. Verify the tool returns expected results
3. Check execution time is reasonable

#### Error Handling
1. Test with missing required fields
2. Test with invalid data types
3. Test with out-of-range values
4. Verify error messages are clear

#### Edge Cases
1. Test with empty strings
2. Test with very large numbers
3. Test with special characters
4. Test with null/undefined values

### 4. Interpreting Results

#### Execution Metadata
- **Status**: Success (green) or Error (red)
- **Duration**: Time taken to execute in milliseconds
- **Time**: Timestamp of when the tool was executed

#### Result Content
- **Success**: Shows the tool's return value formatted as JSON
- **Error**: Shows the error message and error code (if available)

### 5. Common Issues and Solutions

#### "Tool not found" Error
- **Cause**: The tool may be disabled or the server is not active
- **Solution**: Check server status and tool enablement in the main dashboard

#### "Invalid arguments" Error
- **Cause**: Parameters don't match the expected schema
- **Solution**: Review the field descriptions and ensure correct data types

#### "Request timeout" Error
- **Cause**: Tool execution took longer than 30 seconds
- **Solution**: Check if the server is responsive or if the operation is too complex

#### "MCP manager not available" Error
- **Cause**: The MCP manager is not initialized
- **Solution**: Restart the MCP Anywhere service

## Advanced Features

### Clearing Forms and Results
- **Clear Form**: Resets all input fields to empty
- **Clear Results**: Removes the results panel

### Testing Multiple Tools
1. Execute a tool and review results
2. Select a different tool from the list
3. The form will update automatically
4. Previous results remain visible until cleared

### Keyboard Shortcuts
- **Tab**: Navigate between form fields
- **Enter**: Submit the form (when focused on a field)
- **Escape**: Clear the current form (when focused)

## Security Considerations

### Authentication
- You must be logged in to access the testing page
- Unauthenticated users are redirected to the login page

### CSRF Protection
- All tool executions are protected with CSRF tokens
- This prevents cross-site request forgery attacks

### Input Validation
- All inputs are validated on both client and server side
- JSON inputs are parsed and validated before execution
- Invalid inputs are rejected with clear error messages

## Troubleshooting

### Page Won't Load
1. Check if you're logged in
2. Verify the server is running
3. Check browser console for JavaScript errors

### Tools Not Appearing
1. Ensure the server is active
2. Check if tools are enabled in the server settings
3. Verify the server has been built successfully

### Execution Fails
1. Review the error message carefully
2. Check the tool's parameter requirements
3. Verify the server is running and responsive
4. Check the server logs for detailed error information

## Example Workflows

### Example 1: Testing a Weather API

1. Select "Weather API Server"
2. Choose "get_weather" tool
3. Enter city name: "New York"
4. Click "Execute Tool"
5. Review temperature, conditions, and other data
6. Test with different cities

### Example 2: Testing a Database Query

1. Select "Database Tools Server"
2. Choose "query_users" tool
3. Enter filter criteria: `{"age": {"$gt": 18}}`
4. Set limit: `10`
5. Click "Execute Tool"
6. Review returned user records

### Example 3: Testing File Operations

1. Select "File System Server"
2. Choose "read_file" tool
3. Enter file path: `/data/config.json`
4. Click "Execute Tool"
5. Review file contents
6. Test with different file paths

## Feedback and Support

If you encounter issues or have suggestions for improving the testing page:

1. Check the application logs for detailed error information
2. Review the server configuration and status
3. Consult the main MCP Anywhere documentation
4. Report bugs or request features through the project's issue tracker

---

**Happy Testing!** 🚀

