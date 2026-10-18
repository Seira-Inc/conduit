"""Private V8 worker. Only JSON session RPC is exposed to generated JavaScript."""
from __future__ import annotations

import asyncio
import json
import sys

from py_mini_racer import mini_racer

LIMIT = 4 * 1024 * 1024


def send(value):
    sys.stdout.write(json.dumps(value, ensure_ascii=True) + "\n")
    sys.stdout.flush()


async def receive():
    raw = await asyncio.to_thread(sys.stdin.buffer.readline, LIMIT + 1)
    if not raw or len(raw) > LIMIT:
        raise ValueError("Invalid worker message")
    return json.loads(raw)


FACADE = """(host, args, context) => {
  let pending = 0;
  const call = async (method, params) => {
    pending++;
    try {
      const response = JSON.parse(await host(JSON.stringify({method, params})));
      if (response.error) {
        const error = new Error(response.error.message);
        error.code = response.error.code;
        throw error;
      }
      return response.value;
    } finally { pending--; }
  };
  const api = Object.freeze({
    request: options => call('request', options),
    navigate: url => call('navigate', {url}),
    click: locator => call('click', {locator}),
    fill: (locator, value) => call('fill', {locator, value}),
    press: (locator, value) => call('press', {locator, value}),
    select: (locator, value) => call('select', {locator, value}),
    read: (locator, attribute='text') => call('read', {locator, attribute}),
    download: locator => call('download', {locator}),
    upload: (locator, value, options={}) => call('upload', {...options, locator, value}),
    command: (name, args=[]) => call('command', {function:name, arguments:args}),
    draw: (points, modifiers=[]) => call('draw', {points, modifiers}),
    storage: key => call('storage', {key}),
    global: key => call('global', {key}),
    uuid: () => call('uuid', {}),
    sleep: ms => call('sleep', {ms}),
    session: binding => ({$session:binding})
  });
  return {api, input:JSON.parse(args), context:JSON.parse(context),
          finish: value => {
            if (pending) throw Error('Every session operation must be awaited');
            return JSON.stringify(value === undefined ? null : value);
          }};
} """


async def main():
    request = await receive()
    serial = asyncio.Lock()
    sequence = 0

    async def host(raw):
        nonlocal sequence
        if not isinstance(raw, str) or len(raw) > LIMIT:
            raise ValueError("Adapter request exceeds the limit")
        async with serial:
            sequence += 1
            send({"type": "call", "id": sequence, "call": json.loads(raw)})
            response = await receive()
            if response.get("id") != sequence:
                raise ValueError("Unexpected session response")
            return json.dumps(response)

    try:
        with mini_racer() as engine:
            engine.set_hard_memory_limit(96 * 1024 * 1024)
            async with engine.wrap_py_function(host) as bridge:
                factory = await engine.eval_cancelable(FACADE)
                scope = await factory.cancelable()(bridge, json.dumps(request["input"]), json.dumps(request["context"]))
                source = '(async (scope) => { const {api,input,context}=scope; const value=await (async()=>{\n"use strict";\n'
                source += request["javascript"] + '\n})(); return scope.finish(value); })'
                entry = await engine.eval_cancelable(source)
                promise = await entry.cancelable()(scope)
                raw = await promise
                if not isinstance(raw, str) or len(raw) > LIMIT:
                    raise ValueError("Adapter result exceeds the limit")
                send({"type": "result", "value": json.loads(raw)})
    except Exception as exc:
        send({"type": "error", "message": str(exc)[:700]})


if __name__ == "__main__":
    asyncio.run(main())
