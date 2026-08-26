# Usage Guide

This guide provides instructions on how to interact with the Financial Model trading engine.

## Quick Start

To run the scraper in debug mode:
```bash
python src/scraper.py --debug
```

## Configuration

Ensure you have a `.env` file in the root directory with the following configuration:

```env
ALPACA_API_KEY=your_api_key_here
ALPACA_SECRET_KEY=your_secret_key_here
ADANOS_API_KEY=your_adanos_key_here
DATABASE_URL=postgresql://user:password@localhost:5432/dbname
```

## Common Errors

| Error | Cause | Solution |
| :--- | :--- | :--- |
| `AuthenticationError` | Invalid Alpacy or Adanos keys | Check your `.env` file for correct keys |
| `RateLimitError` | Too many requests to the API | Implement a retry logic or increase the polling interval |
| `DatabaseConnectionError` | Database instance is offline or unreachable | Verify your `DATABASE_URL` and ensure the PostgreSQL server is running |

## External Resources

- [Gymnasium Documentation](https://gymnasium.farama.org/)
- [Alpaca Markets Documentation](https://docs.alpaca.markets/)
