# Task List: Fix Price Display and Decision History

## Issues to Resolve

1. **Index boxes showing 0.00 price**: VIX, DJIA, SP500, RUSSELL2000 not displaying correct prices
2. **Symbol cards showing 0.00 price**: AAPL and TSLA not displaying current prices
3. **Decision History not filtering changes**: Shows all updates instead of only when decisions change
4. **Add comprehensive tests**: Create tests for price display and decision history filtering
5. **Run all tests**: Execute test suite to ensure no regressions
6. **Commit changes**: Commit all fixes with proper commit message
7. **Create PR**: Create pull request with all changes

## Implementation Plan

### Task 1: Investigate index price display issue
- Check how market indexes are fetched and displayed
- Verify buffer and database price retrieval
- Identify why indexes show 0.00

### Task 2: Fix index price display
- Update index fetching logic to use correct price source
- Ensure database fallback when market is closed

### Task 3: Fix symbol card price display
- Check how symbol cards fetch prices
- Verify current price is correctly populated from buffer/database
- Test with AAPL/TSLA data

### Task 4: Fix decision history filtering
- Review decision history display logic in frontend
- Implement filtering to only show decision changes
- Ensure proper timestamp comparison

### Task 5: Create tests for price display
- Test index price retrieval and display
- Test symbol card price display
- Test price fallback from database
- Test price updates on new ticks

### Task 6: Create tests for decision history
- Test decision history filtering logic
- Test only showing decision changes
- Test timestamp comparison

### Task 7: Run tests and verify no regressions
- Run full test suite
- Fix any failing tests
- Verify all existing functionality still works

### Task 8: Commit changes
- Stage all changed files
- Write descriptive commit message
- Verify git diff

### Task 9: Create PR
- Push branch to origin
- Create pull request
- Add descriptive PR description