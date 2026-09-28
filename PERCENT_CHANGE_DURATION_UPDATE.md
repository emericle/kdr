# Percentage Change Duration Update

## Overview
Implemented dynamic percentage change display based on user-selected time period for real-time ticks in the dashboard.

## Changes Made

### Backend (`src/web_server.py`)
Modified the `MarketDataBuffer.get_summary()` method to:
- Calculate percentage changes for all supported durations: 1h, 24h, 5d, 30d, 1y, and YTD
- Return additional fields: `changePercent1h`, `changePercent24h`, `changePercent5d`, `changePercent30d`, `changePercent1y`, and `changePercentYTD`
- Use database price lookups for accurate interval calculations, falling back to buffer prices if needed

### Frontend (`src/static/dashboard.html`)
Updated the symbol list and drill-down views to:
- Display duration-specific percentage changes using `item['changePercent' + selectedDuration.value]`
- Update chart color (green/red) based on the selected duration's percentage change
- Dynamically switch between periods when user changes the dropdown selection

## Usage
Users can now select different time periods from the duration dropdown:
- **1 Hour**: Shows percentage change since 1 hour ago
- **24 Hours**: Shows percentage change since 24 hours ago (default)
- **5 Days**: Shows percentage change over the last 5 days
- **30 Days**: Shows percentage change over the last 30 days
- **1 Year**: Shows percentage change over the last year
- **YTD**: Shows percentage change from year-to-date

## Example
For BTC:
- If the current price is $11,000
- Price 1 hour ago: $10,900 → +0.92% (1h)
- Price 24h ago: $10,500 → +4.76% (24h)
- Price 5 days ago: $11,500 → -4.35% (5d)

When user selects "24h", the percentage shown will be +4.76% (green for positive change).

## Implementation Notes
- WebSocket broadcasts now include all duration percentages for real-time updates
- REST API endpoint `/api/symbols/{symbol}` also returns all duration fields
- Backward compatibility maintained with default "24h" duration
- Database queries use the same duration logic as the frontend selection