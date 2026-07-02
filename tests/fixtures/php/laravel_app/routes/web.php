<?php

use App\Http\Controllers\ReportController;

Route::post('/reports', [ReportController::class, 'store']);
