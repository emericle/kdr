#!/bin/bash
# Quick setup wizard for new users

echo "╔════════════════════════════════════════════════════════╗"
echo "║              KDR Quick Setup Wizard                     ║"
echo "╚════════════════════════════════════════════════════════╝"
echo ""

# Check if .env exists
if [ -f .env ]; then
    echo "✅ .env file already exists"
else
    echo "❌ .env file not found"
    echo ""
    echo "Step 1: Create .env file"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""
    echo "Copy the example file:"
    echo "  cp .env.example .env"
    echo ""
    echo "Then edit .env and add your credentials:"
    # bin/check_env.py is the single source of truth for the required list.
    # The wizard runs before the virtualenv exists, so take whichever
    # interpreter this host has; if none of them can run the checker, say where
    # the list lives rather than printing an empty checklist.
    CHECK_PY=""
    if [ -x ".venv/bin/python" ]; then
        CHECK_PY=".venv/bin/python"
    else
        CHECK_PY="$(command -v python3.13 || command -v python3 || true)"
    fi
    REQUIRED_KEYS=""
    if [ -n "$CHECK_PY" ]; then
        REQUIRED_KEYS="$("$CHECK_PY" bin/check_env.py --keys 2>/dev/null)"
    fi
    if [ -n "$REQUIRED_KEYS" ]; then
        for key in $REQUIRED_KEYS; do
            echo "  - $key"
        done
    else
        echo "  (bin/check_env.py could not run; the required names are in .env.example)"
    fi
    echo ""
    read -p "Press Enter to create .env from template (Ctrl+C to cancel)..."
    cp .env.example .env
    echo "✅ .env file created"
    echo ""
fi

# Check if venv exists
if [ -d .venv ]; then
    echo "✅ Virtual environment exists"
    echo ""
else
    echo "❌ Virtual environment not found"
    echo ""
    echo "Step 2: Create virtual environment"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""
    echo "Creating virtual environment..."
    python3.13 -m venv .venv
    if [ $? -eq 0 ]; then
        echo "✅ Virtual environment created"
    else
        echo "❌ Failed to create virtual environment"
        exit 1
    fi
    echo ""
fi

echo "✅ Setup complete!"
echo ""
echo "Next steps:"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "1. Activate virtual environment:"
echo "   source .venv/bin/activate"
echo ""
echo "2. Test your setup:"
echo "   ./tests/test_startup.sh"
echo ""
echo "3. Start KDR:"
echo "   ./start.sh"
echo ""
echo "4. Run with debug mode for more details:"
echo "   ./start.sh --debug"
echo ""
echo "═══════════════════════════════════════════════════════"