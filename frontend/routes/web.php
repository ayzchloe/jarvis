<?php

use Illuminate\Http\Request;
use Illuminate\Support\Facades\Http;
use Illuminate\Support\Facades\Route;

Route::get('/', function () {
    // Never let the browser cache the dashboard shell: UI updates must
    // reach the user on plain refresh, not require hard-refresh tricks.
    return response()
        ->view('dashboard')
        ->header('Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0')
        ->header('Pragma', 'no-cache');
})->name('dashboard');

Route::get('/auth/google/callback', function (Request $request) {
    $backend = rtrim(config('services.jarvis.backend_url'), '/');
    $query = $request->getQueryString();
    try {
        $response = Http::timeout(15)->get("{$backend}/auth/google/callback?{$query}");
        return response($response->body(), $response->status(), [
            'Content-Type' => 'text/html; charset=UTF-8',
        ]);
    } catch (\Throwable $e) {
        return response("<h3>Could not reach JARVIS backend at {$backend}.</h3><p>{$e->getMessage()}</p>", 503);
    }
});
