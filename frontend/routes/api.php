<?php

use Illuminate\Http\Request;
use Illuminate\Support\Facades\Http;
use Illuminate\Support\Facades\Route;

function jarvisBackend(Request $request, string $path, array $payload = []): array
{
    @set_time_limit(300); // Deep mailbox/Drive research can legitimately take minutes
    $backend = rtrim(config('services.jarvis.backend_url'), '/');
    $session = $request->header('X-Jarvis-Session', 'default');

    try {
        $response = Http::timeout(280)
            ->connectTimeout(5)
            ->acceptJson()
            ->withHeaders(['X-Jarvis-Session' => $session])
            ->post("{$backend}{$path}", $payload);
    } catch (\Throwable $error) {
        abort(503, "JARVIS backend is offline. Start main.py first. ({$error->getMessage()})");
    }

    if ($response->failed()) {
        abort($response->status(), $response->json('detail') ?? 'JARVIS backend request failed.');
    }

    return $response->json();
}

// Lightweight polled GET proxy. The PHP dev server handles one request at a
// time, so these must fail fast instead of queueing behind a hung backend.
function jarvisBackendGet(Request $request, string $path, array $query = [], int $timeout = 6): mixed
{
    try {
        return Http::timeout($timeout)
            ->connectTimeout(2)
            ->acceptJson()
            ->withHeaders(['X-Jarvis-Session' => $request->header('X-Jarvis-Session', 'default')])
            ->get(rtrim(config('services.jarvis.backend_url'), '/').$path, $query)
            ->json();
    } catch (\Throwable) {
        return null;
    }
}

Route::post('/command', fn (Request $request) => jarvisBackend($request, '/command', $request->validate(['text' => ['required', 'string', 'max:4000']])));

Route::post('/actions/confirm', fn (Request $request) => jarvisBackend($request, '/actions/confirm', $request->validate(['id' => ['required', 'string'], 'approved' => ['required', 'boolean']])));

Route::post('/speak', fn (Request $request) => jarvisBackend($request, '/speak', $request->validate(['text' => ['required', 'string', 'max:1200']])));

Route::get('/health', function () {
    $data = jarvisBackendGet(request(), '/health', [], 3);
    return $data ?? response()->json(['status' => 'offline'], 503);
});

Route::get('/stats', function () {
    $data = jarvisBackendGet(request(), '/stats', [], 3);
    return $data ?? response()->json(['status' => 'offline'], 503);
});

Route::get('/memory', function (Request $request) {
    $data = jarvisBackendGet($request, '/memory', $request->only(['query', 'limit']));
    return $data ?? response()->json(['count' => 0, 'memories' => []], 503);
});

Route::get('/tasks', function (Request $request) {
    $data = jarvisBackendGet($request, '/tasks', $request->only(['limit']));
    return $data ?? response()->json(['count' => 0, 'tasks' => []], 503);
});

Route::get('/skills', function () {
    $data = jarvisBackendGet(request(), '/skills');
    return $data ?? response()->json(['skills' => []], 503);
});

Route::post('/skills/toggle', fn (Request $request) => jarvisBackend($request, '/skills/toggle', $request->validate(['name' => ['required', 'string', 'max:120'], 'enabled' => ['required', 'boolean']])));

Route::post('/skills/create', fn (Request $request) => jarvisBackend($request, '/skills/create', $request->validate([
    'name' => ['required', 'string', 'regex:/^[a-z_]+$/'],
    'description' => ['required', 'string', 'max:500'],
    'triggers' => ['required', 'array'],
    'prompt' => ['sometimes', 'string', 'max:4000'],
    'steps' => ['sometimes', 'array'],
])));

Route::get('/agents', function () {
    $data = jarvisBackendGet(request(), '/agents', [], 4);
    return $data ?? response()->json(['agents' => []], 503);
});

Route::get('/agents/{agent_id}', function (Request $request, string $agent_id) {
    $data = jarvisBackendGet($request, '/agents/'.urlencode($agent_id), [], 4);
    return $data ?? response()->json(['error' => 'Agent lookup failed.'], 503);
});

Route::post('/agents/spawn', fn (Request $request) => jarvisBackend($request, '/agents/spawn', $request->validate([
    'agent_id' => ['required', 'string', 'max:64'],
    'task' => ['required', 'string', 'max:4000'],
    'autonomy' => ['sometimes', 'string', 'in:low,medium,high'],
])));

Route::post('/agents/run', fn (Request $request) => jarvisBackend($request, '/agents/run', $request->validate([
    'agent_id' => ['required', 'string', 'max:64'],
    'task' => ['required', 'string', 'max:4000'],
    'autonomy' => ['sometimes', 'string', 'in:low,medium,high'],
])));

Route::get('/connectors', function () {
    $data = jarvisBackendGet(request(), '/connectors', [], 3);
    return $data ?? response()->json(['connectors' => [], 'status' => 'offline'], 503);
});

Route::get('/auth/google/url', function (Request $request) {
    $data = jarvisBackendGet($request, '/auth/google/url', $request->only(['redirect_uri']), 5);
    if (!is_array($data) || empty($data['url'])) {
        $reason = is_array($data) ? ($data['error'] ?? 'No URL returned') : 'JARVIS backend is offline. Start main.py first.';
        return response()->json(['error' => $reason], 503);
    }
    return $data;
});

Route::get('/auth/google/status', function () {
    $data = jarvisBackendGet(request(), '/auth/google/status', [], 4);
    return $data ?? response()->json(['error' => 'JARVIS backend is offline.', 'connected' => false], 503);
});

Route::post('/auth/google/disconnect', function (Request $request) {
    return jarvisBackend($request, '/auth/google/disconnect');
});

// Generic connector OAuth / token endpoints (slack, linkedin, upwork,
// whatsapp, instagram, notion)
foreach (['slack', 'linkedin', 'upwork', 'whatsapp', 'instagram', 'notion'] as $connectorId) {
    Route::get("auth/{$connectorId}/url", function (Request $request) use ($connectorId) {
        $data = jarvisBackendGet($request, "/auth/{$connectorId}/url", $request->only(['redirect_uri']), 5);
        if (!is_array($data) || empty($data['url'])) {
            $reason = is_array($data) ? ($data['detail'] ?? 'No URL returned') : 'JARVIS backend is offline. Start main.py first.';
            return response()->json(['error' => $reason], 503);
        }
        return $data;
    });

    Route::get("auth/{$connectorId}/status", function () use ($connectorId) {
        $data = jarvisBackendGet(request(), "/auth/{$connectorId}/status", [], 4);
        return $data ?? response()->json(['error' => 'JARVIS backend is offline.', 'status' => 'unknown'], 503);
    });

    Route::post("auth/{$connectorId}/disconnect", function (Request $request) use ($connectorId) {
        return jarvisBackend($request, "/auth/{$connectorId}/disconnect");
    });

    Route::get("connectors/test/{$connectorId}", function () use ($connectorId) {
        $data = jarvisBackendGet(request(), "/connectors/test/{$connectorId}", [], 8);
        return $data ?? response()->json(['error' => 'JARVIS backend is offline.', 'ok' => null], 503);
    });
}

Route::post('/model/set', function (Request $request) {
    return jarvisBackend($request, '/model/set', $request->validate(['provider' => ['required', 'string', 'in:groq,gemini'], 'model' => ['required', 'string']]));
});

Route::get('/model/get', function () {
    $data = jarvisBackendGet(request(), '/model/get', [], 3);
    return $data ?? response()->json(['provider' => 'groq', 'model' => 'openai/gpt-oss-20b'], 503);
});

Route::get('/models', function () {
    $data = jarvisBackendGet(request(), '/models', [], 4);
    return $data ?? response()->json(['groq' => [], 'gemini' => []], 503);
});
