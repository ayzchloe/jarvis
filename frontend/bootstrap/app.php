<?php

use Illuminate\Foundation\Application;
use Illuminate\Foundation\Configuration\Exceptions;
use Illuminate\Foundation\Configuration\Middleware;

return Application::configure(basePath: dirname(__DIR__))
    ->withRouting(
        web: __DIR__.'/../routes/web.php',
        api: __DIR__.'/../routes/api.php',
        health: '/up',
    )
    ->withMiddleware(function (Middleware $middleware) {
        // Web requests use Laravel's default CSRF middleware.
    })
    ->withExceptions(function (Exceptions $exceptions) {
        // Default Laravel exception rendering is sufficient for this local app.
    })
    ->create();
