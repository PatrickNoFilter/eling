/**
 * Eling Memory Plugin for OpenCode
 *
 * Auto-writes session memory to eling (facts + Notion), mirroring
 * Hermes's MemoryProvider.sync_turn() behavior.
 *
 * Hooks:
 *   chat.message      → store user prompt as fact
 *   tool.execute.after → store tool observation as fact
 *   event (session.idle) → push high-trust facts to Notion
 *   event (session.compacted) → snapshot highlights + push
 */
module.exports = async function (input, options) {
  const $ = input.$;

  function eling(args) {
    const cmd = `python3 -m eling ${args}`;
    $`${cmd}`.then(() => {}).catch((err) => {
      console.error(`[eling] FAILED: ${cmd} — ${err.message || err}`);
    });
  }

  function elingSync(args) {
    const cmd = `python3 -m eling ${args}`;
    $`${cmd}`.then(() => {}).catch((err) => {
      console.error(`[eling] FAILED: ${cmd} — ${err.message || err}`);
    });
  }

  return {
    "chat.message": async (_input, output) => {
      const parts = output?.parts || [];
      const text = parts
        .filter((p) => p?.type === "text")
        .map((p) => p.text)
        .join(" ")
        .slice(0, 500);
      if (text) {
        eling(
          `remember ${JSON.stringify(text)} --category user_prompt --source opencode`,
        );
      }
    },

    "tool.execute.after": async (input, output) => {
      const tool = input?.tool || "unknown";
      const result = output?.title || output?.output || "";
      const text = `Tool [${tool}] returned: ${String(result).slice(0, 300)}`;
      eling(
        `remember ${JSON.stringify(text)} --category tool_observation --tags ${tool} --source opencode`,
      );
    },

    event: async ({ event }) => {
      if (event?.type === "session.idle") {
        elingSync("sync --direction push");
      }
      if (event?.type === "session.compacted") {
        elingSync("sync --direction push");
      }
    },
  };
};
