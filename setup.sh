#!/bin/bash
# setup.sh — Interactive setup for NineKiroBotOS
# Creates .env, generates agent identity, installs dependencies

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"
TEMPLATE="$SCRIPT_DIR/.kiro/agents/Template.md"

echo ""
echo "╔══════════════════════════════════════╗"
echo "║     NineKiroBotOS Setup Wizard       ║"
echo "╚══════════════════════════════════════╝"
echo ""

# ── Helper ────────────────────────────────────────────────────────────────────
ask() {
  local var="$1"
  local prompt="$2"
  local default="$3"
  local value=""
  if [ -n "$default" ]; then
    read -r -p "  $prompt [$default]: " value
    value="${value:-$default}"
  else
    while [ -z "$value" ]; do
      read -r -p "  $prompt: " value
      [ -z "$value" ] && echo "  ⚠️  This field is required."
    done
  fi
  eval "$var=\"$value\""
}

# ── Collect info ──────────────────────────────────────────────────────────────
echo "📋 Bot Identity"
echo "───────────────"
ask BOT_NAME      "Bot name (English, no spaces, e.g. MyCoolBot)"
ask BotNickName   "Bot nickname (display name, e.g. 小明)"
ask BOT_DESCRIPTION "Bot description (one line)"
ask BOT_ROLE      "Bot role (what does this bot do?)"

echo ""
echo "🔑 Telegram Credentials"
echo "───────────────────────"
ask BOT_TOKEN     "Bot Token (from @BotFather)"
ask ALLOWED_USER_ID "Your Telegram User ID (from @userinfobot)" "6246433369"

echo ""
echo "📢 Channel / Group Settings (optional, press Enter to skip)"
echo "────────────────────────────────────────────────────────────"
read -r -p "  Allowed Group IDs (comma-separated, e.g. -100123,-100456): " ALLOWED_GROUP_IDS
read -r -p "  Allowed Channel IDs (comma-separated): " ALLOWED_CHANNEL_IDS
read -r -p "  CC Channel IDs for /cc command (comma-separated): " CC_CHANNEL_IDS

echo ""
echo "⚙️  Options"
echo "──────────"
ask AUTO_RESTART  "Auto-restart on crash? (true/false)" "true"
ask HANDLE_IMAGE  "Handle image messages? (true/false)" "true"
ask HANDLE_FILE   "Handle file messages? (true/false)" "true"

echo ""
echo "📁 Working Directory (optional)"
echo "────────────────────────────────"
echo "  By default, each chat session gets its own folder inside sessions/."
echo "  You can force all sessions to use a specific path (e.g. your project folder)."
read -r -p "  Force working directory (leave empty for default): " AGENT_WORK_DIR

# ── Write .env ────────────────────────────────────────────────────────────────
cat > "$ENV_FILE" <<EOF
BOT_NAME=$BOT_NAME
BOT_DESCRIPTION=$BOT_DESCRIPTION
BotNickName=$BotNickName
BOT_ROLE=$BOT_ROLE

BOT_TOKEN=$BOT_TOKEN
ALLOWED_USER_ID=$ALLOWED_USER_ID

ALLOWED_GROUP_IDS=$ALLOWED_GROUP_IDS
ALLOWED_CHANNEL_IDS=$ALLOWED_CHANNEL_IDS
CC_CHANNEL_IDS=$CC_CHANNEL_IDS

AUTO_RESTART=$AUTO_RESTART
HANDLE_IMAGE=$HANDLE_IMAGE
HANDLE_FILE=$HANDLE_FILE

# Optional: Force all sessions to use a specific working directory
AGENT_WORK_DIR=$AGENT_WORK_DIR
EOF

echo ""
echo "✅ .env created"

# ── Generate agent from Template ──────────────────────────────────────────────
echo "🤖 Generating agent identity..."

AGENT_MD="$SCRIPT_DIR/.kiro/agents/${BOT_NAME}.md"

sed \
  -e "s|{BotName}|$BOT_NAME|g" \
  -e "s|{BotDescription}|$BOT_DESCRIPTION|g" \
  -e "s|{BotNickName}|$BotNickName|g" \
  -e "s|{BotRole}|$BOT_ROLE|g" \
  "$TEMPLATE" > "$AGENT_MD"

echo "✅ Generated: $AGENT_MD"

# Convert to JSON for kiro-cli
AGENT_JSON="$HOME/.kiro/agents/${BOT_NAME}.json"
mkdir -p "$HOME/.kiro/agents"

PROMPT=$(python3 -c "
import json
content = open('$AGENT_MD', encoding='utf-8', errors='replace').read()
content = content.encode('utf-8', errors='replace').decode('utf-8', errors='replace')
parts = content.split('---', 2)
prompt = parts[2].strip() if len(parts) >= 3 else content.strip()
print(json.dumps(prompt, ensure_ascii=False))
")

python3 -c "
import json
data = {
  'name': '$BOT_NAME',
  'description': '$BOT_DESCRIPTION',
  'prompt': $PROMPT,
  'mcpServers': {},
  'tools': ['*'],
  'toolAliases': {},
  'allowedTools': [],
  'resources': [],
  'hooks': {},
  'toolsSettings': {},
  'includeMcpJson': True,
  'model': None
}
print(json.dumps(data, ensure_ascii=False, indent=2))
" > "$AGENT_JSON"

echo "✅ Installed: $AGENT_JSON"

# Validate
if command -v kiro-cli &>/dev/null; then
  kiro-cli agent validate --path "$AGENT_JSON" && echo "✅ Agent validated!" || echo "⚠️  Validation failed, check $AGENT_JSON"
else
  echo "⚠️  kiro-cli not found, skipping validation"
fi

# ── Install Python deps ───────────────────────────────────────────────────────
echo ""
echo "📦 Installing Python dependencies..."
pip3 install -r "$SCRIPT_DIR/requirements.txt" --quiet && echo "✅ Dependencies installed"

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════╗"
echo "║           Setup Complete! 🎉         ║"
echo "╚══════════════════════════════════════╝"
echo ""
echo "  Start your bot:"
echo "  python3 bot.py --agent $BOT_NAME"
echo ""
