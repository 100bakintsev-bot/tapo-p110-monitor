# Встановлення

## Linux

Потрібні Python 3.11+ із venv, системна база часових поясів і локальний доступ до розетки. Публічний пакет орієнтований на POSIX; нативний Windows не підтримує використані flock/сигнали.

Завантажте проєкт в окремий каталог. У його корені:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
chmod +x run
./run configure --host YOUR_PLUG_IP
./run
```

Для збору в поточному терміналі:

```sh
.venv/bin/python -B monitor.py
```

Для роботи як служби використовуйте supervisor своєї системи з командою `.venv/bin/python -B monitor_service.py` та абсолютним шляхом до проєкту. Під час завершення адаптер закриває дочірню групу процесів.

## Android / Termux з Debian

У Termux установіть потрібні пакети:

```sh
pkg install python proot-distro termux-services
```

Якщо Debian ще немає, установіть його:

```sh
proot-distro install debian
```

Не перевстановлюйте вже наявний контейнер. Усередині Debian потрібні `python3`, `python3-venv`, `python3-pip`, `tzdata`; за потреби встановіть їх стандартним менеджером пакетів Debian.

Розмістіть проєкт у `$HOME/tapo-p110-monitor`. Створіть окреме середовище, не змінюючи залежності наявного бота:

```sh
cd "$HOME/tapo-p110-monitor"
proot-distro login debian -- python3 -m venv "$PWD/.venv"
proot-distro login debian -- "$PWD/.venv/bin/python" -m pip install -r "$PWD/requirements.txt"
chmod +x run
./run configure --host YOUR_PLUG_IP
./run
```

`run` сам обирає Debian, коли запущений із Termux. Каталог даних явно передається всередину proot, тому він не змінюється на `/root`.

## Служба Termux

Не запускайте цей збирач паралельно з іншим збирачем для тієї самої розетки. Для вже встановленої приватної версії це інструкція міграції, а не команда встановити ще одну копію.

Після встановлення termux-services відкрийте нову сесію Termux. Створіть `$PREFIX/var/service/tapo-p110-monitor/run` з таким вмістом:

```sh
#!/data/data/com.termux/files/usr/bin/sh
export TAPO_DATA_DIR="$HOME/.local/share/tapo-p110-monitor"
export TAPO_TIMEZONE="Europe/Kyiv"
exec "$PREFIX/bin/python" -B "$HOME/tapo-p110-monitor/monitor_service.py"
```

Надайте дозвіл виконання й увімкніть службу:

```sh
chmod 700 "$PREFIX/var/service/tapo-p110-monitor/run"
sv-enable tapo-p110-monitor
sv status "$PREFIX/var/service/tapo-p110-monitor"
```

Тимчасовий перезапуск:

```sh
sv -w 20 restart "$PREFIX/var/service/tapo-p110-monitor"
```

Постійна зупинка:

```sh
sv-disable tapo-p110-monitor
```

Runit перезапускає завершений процес, поки служба увімкнена. Він не може запуститися, якщо Android завершив увесь Termux. Для старту після перезавантаження потрібна наявна конфігурація Termux:Boot/запуску termux-services та дозволи Android. Проєкт не змінює системні обмеження Android.

## Облікові дані й резервна копія

`credentials.json` зберігає email, пароль і адресу пристрою локально з правами 600. Захист — файлові дозволи, а не шифрування. Не використовуйте публічний/синхронізований каталог для `TAPO_DATA_DIR`.

Перед оновленням зупиніть службу, збережіть код і каталог даних у приватне місце, замініть код, запустіть тести та службу. За працюючої служби копіюйте SQLite через SQLite backup API, а не лише головний файл бази без WAL.

Повторний `configure` потрібен після зміни облікових даних або адреси. Якщо одноразовий читач повідомляє `Reader already running`, повторіть спробу між циклами або тимчасово зупиніть збирач.
