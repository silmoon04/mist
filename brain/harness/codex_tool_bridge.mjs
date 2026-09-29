/** JSONL adapter for the existing robot extension. No shell or hardware tools. */
import readline from 'node:readline';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { Check } from 'typebox/value';
import { readCapabilitySnapshot } from '../extensions/grounding.ts';

process.env.ROBOT_BRAIN_DIR ??= path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
process.env.ROBOT_RUN_DIR ??= path.join(process.env.ROBOT_BRAIN_DIR, 'results', 'run-current');
const { default: register } = await import('../extensions/robot-tools.ts');

const allowlist = new Set([
  'walk', 'drive', 'turn', 'move', 'pose', 'dance', 'face', 'look_at', 'stop',
  'list_skills', 'load_skill', 'note', 'ask_executive', 'check_inbox', 'get_status',
  'get_capabilities', 'observe', 'recall_memory',
]);
const handlers = new Map();
register({ registerTool(tool) {
  if (!allowlist.has(tool.name) || handlers.has(tool.name)) throw new Error('Unexpected robot tool registration');
  handlers.set(tool.name, tool);
} });
if (handlers.size !== allowlist.size) throw new Error('Robot tool registry is incomplete');

const send = (value) => process.stdout.write(JSON.stringify(value) + '\n');
const lines = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
for await (const line of lines) {
  let request;
  try {
    if (line.length > 65_536) throw new Error('Tool request is too large');
    request = JSON.parse(line);
    if (request.method === 'list') {
      send({ id: request.id, result: [...handlers.values()].map((tool) => ({
        name: tool.name, description: tool.description,
        inputSchema: { ...tool.parameters, additionalProperties: false },
      })) });
      continue;
    }
    if (request.method === 'context') {
      send({ id: request.id, result: readCapabilitySnapshot(process.env.ROBOT_RUN_DIR), isError: false });
      continue;
    }
    if (request.method !== 'call') throw new Error('Unknown bridge method');
    const tool = handlers.get(request.name);
    if (!tool) throw new Error('Tool is not in the robot allowlist');
    const schema = { ...tool.parameters, additionalProperties: false };
    if (!Check(schema, request.arguments)) throw new Error('Tool arguments do not match the robot schema');
    const result = await tool.execute(String(request.id), request.arguments);
    send({ id: request.id, result, isError: false });
  } catch (error) {
    send({ id: request?.id ?? null, result: { content: [{ type: 'text', text: String(error.message) }] }, isError: true });
  }
}
