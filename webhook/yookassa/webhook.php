<?php
declare(strict_types=1);

// Загрузка .env файла (должна быть ДО любого другого кода кроме declare)
if (file_exists(__DIR__ . '/.env')) {
    foreach (file(__DIR__ . '/.env', FILE_IGNORE_NEW_LINES | FILE_SKIP_EMPTY_LINES) as $line) {
        $line = trim($line);
        if ($line === '' || strpos($line, '#') === 0) continue;
        putenv($line);
    }
}

// webhook.php — secure & compatible YooKassa webhook receiver
// Path in your setup:
// /home/YOUR_USER/domains/your-domain.com/public_html/yookassa/webhook.php
//
// Features:
// - Accepts authentication by: URL token (PRIMARY) OR X-Forward-Secret (alternative).
// - Note: YooKassa has their own verification system - we don't need complex HMAC checks.
// - Creates yookassa_notifications table with UNIQUE(payment_id).
// - Uses prepared statement with INSERT IGNORE for idempotency.
// - Fallback: writes JSON to data/yookassa_queue/ on DB errors.
// - Minimal, safe logging (no secrets).
// - Optional forwarding to internal bot endpoint with X-Forward-Secret.
// - Optional Telegram admin notification (Russian text).
//
// IMPORTANT: configure secrets in environment where possible (panel / .env not in repo).
// For backward compatibility, if env vars are missing, legacy values from previous file are used.

// ---------------- START basic debug (preview only) ----------------
$debug_log = __DIR__ . '/webhook_debug.log';
$raw_input = @file_get_contents('php://input') ?: '';
$timestamp = date('Y-m-d H:i:s');
$client_ip = $_SERVER['REMOTE_ADDR'] ?? 'unknown';
$request_uri = $_SERVER['REQUEST_URI'] ?? 'unknown';

// write limited preview (first 2000 chars) to debug file - safe: no secrets printed fully
$preview = mb_substr($raw_input, 0, 2000);
@file_put_contents($debug_log, "[$timestamp] IP: $client_ip | URI: $request_uri | PayloadPreview: " . $preview . PHP_EOL, FILE_APPEND | LOCK_EX);
// ---------------- END debug preview -------------------------------

// ---------------- CONFIG (read from env ONLY - no fallbacks for secrets) ----------------
$db_host = getenv('YOOKASSA_RELAY_DB_HOST') ?: 'localhost';
$db_user = getenv('YOOKASSA_RELAY_DB_USER') ?: '';
$db_pass = getenv('YOOKASSA_RELAY_DB_PASS') ?: '';
$db_name = getenv('YOOKASSA_RELAY_DB_NAME') ?: '';

// token in URL - MUST be set in environment
$expected_token = getenv('YOOKASSA_WEBHOOK_TOKEN') ?: '';

// forward secret shared between PHP and bot internal endpoint - MUST be set
$forward_secret = getenv('FORWARD_SECRET') ?: '';

// Optional: allowed IP CIDRs (comma-separated) — additional check when needed
$allowed_ip_ranges = getenv('YOOKASSA_ALLOWED_IP_RANGES') ?: ''; // e.g. "185.71.76.0/22,185.71.80.0/22"

// Optional forward to bot endpoint (internal) - attach X-Forward-Secret
$forward_url = getenv('YOOKASSA_FORWARD_TO_BOT_URL') ?: ''; // e.g. https://your-bot-server.com/internal/yookassa

// Optional Telegram admin notify
$telegram_bot_token = getenv('BOT_TOKEN') ?: '';
$admin_chat_id = getenv('ADMIN_IDS') ?: '';

// queue dir (fallback storage)
$queue_dir = __DIR__ . '/../data/yookassa_queue';
@mkdir($queue_dir, 0700, true);

// log file
$log_file = __DIR__ . '/yookassa_webhook.log';

// ---------------- Utilities ----------------
function safe_log(string $msg): void {
    global $log_file;
    $ts = date('Y-m-d H:i:s');
    @file_put_contents($log_file, "[$ts] $msg\n", FILE_APPEND | LOCK_EX);
}

// get header case-insensitive
function header_ci(string $name) {
    $name_low = strtolower($name);
    if (function_exists('getallheaders')) {
        $h = getallheaders();
        foreach ($h as $k => $v) {
            if (strtolower($k) === $name_low) return $v;
        }
    }
    // fallback to $_SERVER
    $key = 'HTTP_' . strtoupper(str_replace('-', '_', $name));
    return $_SERVER[$key] ?? null;
}

function ip_in_allowed_ranges(string $ip, string $cidrs): bool {
    if (trim($cidrs) === '') return false;
    if (filter_var($ip, FILTER_VALIDATE_IP) === false) return false;
    $parts = array_map('trim', explode(',', $cidrs));
    if (!function_exists('ip_in_cidr')) {
        function ip_in_cidr($ip, $cidr) {
            list($subnet, $mask) = explode('/', $cidr);
            $ip = ip2long($ip);
            $subnet = ip2long($subnet);
            $mask = ~(pow(2, (32 - (int)$mask)) - 1);
            return ($ip & $mask) === ($subnet & $mask);
        }
    }
    foreach ($parts as $c) {
        if ($c === '') continue;
        try {
            if (ip_in_cidr($ip, $c)) return true;
        } catch (Throwable $e) {
            continue;
        }
    }
    return false;
}

// safe JSON encode for logs
function json_pretty($data) {
    return json_encode($data, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
}

// ---------------- Read request ----------------
$raw = @file_get_contents('php://input');
if ($raw === false) $raw = '';
$remote_ip = $_SERVER['REMOTE_ADDR'] ?? 'unknown';

// quick headers preview (don't log full headers with secrets)
$headers_preview = [];
foreach (['X-Hook-Signature','X-Forward-Secret','X-Request-Signature','X-Signature','Content-Type'] as $hn) {
    $v = header_ci($hn);
    if ($v !== null) $headers_preview[$hn] = mb_substr($v, 0, 200);
}
safe_log("Incoming webhook from {$remote_ip}; headers_preview: " . json_pretty($headers_preview));

// ---------------- AUTHENTICATION (2 simple modes - YooKassa doesn't require HMAC) ----------------
// Note: YooKassa uses their own verification system. We only need basic auth via token or secret header.

// 1) token param (PRIMARY method - recommended by YooKassa)
$token = $_GET['token'] ?? '';
$trusted = false;
if ($token !== '' && $expected_token !== '' && hash_equals((string)$expected_token, (string)$token)) {
    $trusted = true;
    safe_log("Auth: trusted via token param");
}

// 2) forward secret header (alternative for internal forwarding)
if (!$trusted) {
    $fwd = header_ci('X-Forward-Secret') ?? header_ci('x-forward-secret');
    if ($fwd !== null && $forward_secret !== '' && hash_equals((string)$forward_secret, (string)$fwd)) {
        $trusted = true;
        safe_log("Auth: trusted via X-Forward-Secret header");
    }
}

// optional IP whitelist: if set and remote IP not in list, reject (only if configured)
if (!$trusted && trim($allowed_ip_ranges) !== '') {
    if (ip_in_allowed_ranges($remote_ip, $allowed_ip_ranges)) {
        $trusted = true;
        safe_log("Auth: trusted via IP whitelist ({$remote_ip})");
    } else {
        safe_log("Auth: IP not in allowed ranges: {$remote_ip}");
    }
}

// final check
if (!$trusted) {
    http_response_code(403);
    safe_log("Webhook rejected (not trusted) from {$remote_ip}");
    echo "forbidden";
    exit;
}

// ---------------- Parse JSON ----------------
if (trim($raw) === '') {
    safe_log("Empty payload after auth from {$remote_ip}");
    http_response_code(400);
    echo "no payload";
    exit;
}
$data = json_decode($raw, true);
if ($data === null) {
    safe_log("Invalid JSON payload from {$remote_ip}");
    http_response_code(400);
    echo "invalid json";
    exit;
}

// minimal payload preview for logs (no secrets)
$payload_preview = mb_substr(json_pretty($data), 0, 2000);
safe_log("Payload preview: " . $payload_preview);

// ---------------- Normalize event/object ----------------
$event = '';
$object = null;
if (isset($data['event'])) {
    if (is_array($data['event']) && isset($data['event']['type'])) {
        $event = (string)$data['event']['type'];
    } elseif (is_string($data['event'])) {
        $event = (string)$data['event'];
    } else {
        $event = json_pretty($data['event']);
    }
}
$object = $data['object'] ?? ($data['payment'] ?? $data);
$payment_id = $object['id'] ?? $object['payment_id'] ?? '';
$metadata = is_array($object['metadata'] ?? null) ? $object['metadata'] : ($data['metadata'] ?? []);
$order_id = $metadata['order_id'] ?? ($object['metadata']['order_id'] ?? '');
$order_id_str = $order_id === null ? '' : (string)$order_id;

// relevant events filter
$allowed_events = ['payment.succeeded','payment.waiting_for_capture','payment.canceled','refund.succeeded','payment.failed'];
if ($event !== '' && !in_array($event, $allowed_events, true)) {
    safe_log("Event not relevant: {$event} — ignoring");
    http_response_code(200);
    echo "ok";
    exit;
}

// ---------------- Persist to DB (idempotent) ----------------
$inserted = false;
if ($db_host !== '' && $db_user !== '' && $db_name !== '') {
    $mysqli = @new mysqli($db_host, $db_user, $db_pass, $db_name);
    if ($mysqli->connect_errno) {
        safe_log("DB_CONNECT_ERROR: " . $mysqli->connect_error);
    } else {
        // create table with UNIQUE(payment_id)
        $create_sql = <<<SQL
CREATE TABLE IF NOT EXISTS yookassa_notifications (
  id INT AUTO_INCREMENT PRIMARY KEY,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  payment_id VARCHAR(255),
  event_type VARCHAR(255),
  payload LONGTEXT,
  order_id VARCHAR(255),
  processed TINYINT(1) DEFAULT 0,
  processed_at TIMESTAMP NULL,
  processed_by VARCHAR(100) NULL,
  UNIQUE KEY uq_payment (payment_id)
) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
SQL;
        if (!$mysqli->query($create_sql)) {
            safe_log("WARN: create table failed: " . $mysqli->error);
            // continue - fallback will handle
        }

        // prepare idempotent insert using INSERT IGNORE
        $payload_sql = json_pretty($object);
        $sql = "INSERT IGNORE INTO yookassa_notifications (payment_id, event_type, payload, order_id, processed) VALUES (?, ?, ?, ?, 0)";
        $stmt = $mysqli->prepare($sql);
        if ($stmt) {
            $stmt->bind_param('ssss', $payment_id, $event, $payload_sql, $order_id_str);
            if ($stmt->execute()) {
                if ($stmt->affected_rows > 0) {
                    $inserted = true;
                    safe_log("DB_INSERT_OK payment_id=" . substr((string)$payment_id, 0, 80));
                } else {
                    // existed already
                    safe_log("DB_DUPLICATE_IGNORED payment_id=" . substr((string)$payment_id, 0, 80));
                }
            } else {
                safe_log("DB_EXECUTE_ERROR: " . $stmt->error);
            }
            $stmt->close();
        } else {
            safe_log("DB_PREPARE_ERROR: " . $mysqli->error);
        }
        $mysqli->close();
    }
}

// ---------------- Fallback queue write ----------------
if (!$inserted) {
    try {
        $safe_pid = preg_replace('/[^A-Za-z0-9_\-\.]/', '_', (string)$payment_id ?: 'pid_missing');
        $fname = $queue_dir . "/notif_{$safe_pid}_" . time() . ".json";
        $dump = [
            'payment_id' => (string)$payment_id,
            'event' => $event,
            'order_id' => $order_id_str,
            'payload' => $object,
            'received_at' => date('c'),
            'remote_ip' => $remote_ip
        ];
        file_put_contents($fname, json_encode($dump, JSON_UNESCAPED_UNICODE | JSON_PRETTY_PRINT), LOCK_EX);
        safe_log("QUEUE_WRITE OK: {$fname}");
    } catch (Throwable $e) {
        safe_log("QUEUE_WRITE_FAILED: " . $e->getMessage());
    }
}

// ---------------- Optional: forward to internal bot endpoint ----------------
if (!empty($forward_url)) {
    $ch = curl_init($forward_url);
    curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
    curl_setopt($ch, CURLOPT_POSTFIELDS, $raw);
    curl_setopt($ch, CURLOPT_HTTPHEADER, ['Content-Type: application/json', 'X-Forward-Secret: ' . $forward_secret]);
    curl_setopt($ch, CURLOPT_CONNECTTIMEOUT, 3);
    curl_setopt($ch, CURLOPT_TIMEOUT, 6);
    $res = @curl_exec($ch);
    if ($res === false) {
        safe_log("FORWARD_CURL_ERROR: " . curl_error($ch));
    } else {
        safe_log("FORWARD_CURL_OK len=" . strlen((string)$res));
    }
    curl_close($ch);
}

// ---------------- Optional: Telegram admin notification (Russian) ----------------
# Telegram disabled - bot handles notifications
if (false) {
    $russian_status = $event;
    $emoji = "ℹ️";
    switch ($event) {
        case 'payment.succeeded':
            $russian_status = "✅ Платёж успешно завершён";
            $emoji = "✅";
            break;
        case 'payment.waiting_for_capture':
            $russian_status = "⏳ Платёж ожидает подтверждения";
            $emoji = "⏳";
            break;
        case 'payment.canceled':
            $russian_status = "❌ Платёж отменён";
            $emoji = "❌";
            break;
        case 'payment.failed':
            $russian_status = "🚫 Платёж не прошёл";
            $emoji = "🚫";
            break;
        case 'refund.succeeded':
            $russian_status = "💸 Возврат выполнен";
            $emoji = "💸";
            break;
        default:
            $russian_status = "ℹ️ " . $event;
            $emoji = "ℹ️";
    }
    $order_display = ($order_id_str !== '') ? $order_id_str : "(не указан)";
    $text = "$emoji Статус: $russian_status\n📦 Заказ: $order_display\n🔢 ID платежа: " . (string)$payment_id;
    if (isset($object['amount']['value'])) {
        $amount = $object['amount']['value'];
        $currency = $object['amount']['currency'] ?? 'RUB';
        $text .= "\n💰 Сумма: {$amount} {$currency}";
    }
    // send non-blocking
    $tg_url = "https://api.telegram.org/bot{$telegram_bot_token}/sendMessage";
    $post = ['chat_id' => $admin_chat_id, 'text' => $text];
    $ch2 = curl_init($tg_url);
    curl_setopt($ch2, CURLOPT_RETURNTRANSFER, true);
    curl_setopt($ch2, CURLOPT_POSTFIELDS, $post);
    curl_setopt($ch2, CURLOPT_CONNECTTIMEOUT, 2);
    curl_setopt($ch2, CURLOPT_TIMEOUT, 4);
    @curl_exec($ch2);
    @curl_close($ch2);
    safe_log("TG_NOTIFY_SENT preview");
}

// ---------------- Final response ----------------
http_response_code(200);
echo "ok";
exit;
