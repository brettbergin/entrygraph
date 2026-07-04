<?php
function confirmed() {
    $q = $_GET['cmd'];
    system($q);
}

function refuted() {
    $q = $_GET['cmd'];
    system("ls");
}
