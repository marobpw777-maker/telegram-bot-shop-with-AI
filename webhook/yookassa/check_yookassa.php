<?php
declare(strict_types=1);

// Подключение к базе (данные из .env / переменных окружения)
$mysqli = new mysqli(
    getenv('YOOKASSA_RELAY_DB_HOST') ?: 'localhost',
    getenv('YOOKASSA_RELAY_DB_USER') ?: 'your_db_user',
    getenv('YOOKASSA_RELAY_DB_PASS') ?: '',
    getenv('YOOKASSA_RELAY_DB_NAME') ?: 'your_db_name'
);

// Проверка соединения
if ($mysqli->connect_errno) {
    die("Failed to connect to MySQL: " . $mysqli->connect_error);
}

// Пример: берем последние 5 платежей
$sql = "SELECT id, status, metadata FROM yookassa_payments ORDER BY created_at DESC LIMIT 5";

if ($result = $mysqli->query($sql)) {
    while ($row = $result->fetch_assoc()) {
        echo "ID: " . $row['id'] . "\n";
        echo "Status: " . $row['status'] . "\n";
        echo "Metadata: " . $row['metadata'] . "\n";
        echo "--------------------------\n";
    }
    $result->free();
} else {
    echo "Query failed: " . $mysqli->error;
}

$mysqli->close();
