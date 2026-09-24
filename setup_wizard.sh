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
    echo "  - ADANOS_API_KEY"
    echo "  - ALPACA_API_KEY"
    echo "  - ALPACA_SECRET_KEY"
    echo "  - DATABASE_URL"
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
    python3.14 -m venv .venv
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
echo "   python test_startup.py"
echo ""
echo "3. Start KDR:"
echo "   ./start.sh"
echo ""
echo "4. Run with debug mode for more details:"
echo "   ./start.sh --debug"
echo ""
echo "═══════════════════════════════════════════════════════"