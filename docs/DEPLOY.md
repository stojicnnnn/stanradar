# Running stanradar 24/7 on a VPS

Written for: whoever is setting the server up — you, or me if you hand over SSH access.

The whole point of this tool is to be awake when you are not. Any always-on Linux box does
the job; a €4–5/month VPS is the cheapest reliable one. Tested against Ubuntu 24.04 LTS,
which is what Hetzner, Contabo and DigitalOcean all offer by default.

Resource needs are tiny: five HTTP requests a minute, a SQLite file that grows by a few MB
a month. **The smallest instance any provider sells is more than enough** — 1 vCPU and
1 GB RAM. Do not pay for more.

## 0. Create the server

Hetzner CX22 (Nuremberg or Falkenstein — both are ~20 ms from Belgrade) or Contabo VPS S.
Pick Ubuntu 24.04. Add your SSH public key during creation rather than using a password;
if you don't have one yet, on Windows run `ssh-keygen -t ed25519` and paste the contents of
`C:\Users\sniko\.ssh\id_ed25519.pub`.

Then SSH in as root and run the rest.

## 1. Harden, in the five minutes it takes

Nothing here is optional on a box with a public IP.

```bash
# A non-root user to actually run the thing.
adduser --disabled-password --gecos "" stanradar
install -d -m 700 -o stanradar -g stanradar /home/stanradar/.ssh
cp ~/.ssh/authorized_keys /home/stanradar/.ssh/authorized_keys
chown stanradar:stanradar /home/stanradar/.ssh/authorized_keys
chmod 600 /home/stanradar/.ssh/authorized_keys

# Keys only, no root login.
sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config
sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
systemctl restart ssh

# stanradar makes only outbound connections, so block all inbound except SSH.
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw --force enable

# Security patches without you remembering to apply them.
apt update && apt install -y unattended-upgrades
dpkg-reconfigure -f noninteractive unattended-upgrades

# Belgrade time, so log timestamps match the listing timestamps you are reading.
timedatectl set-timezone Europe/Belgrade
```

Confirm you can still get in from a *second* terminal before closing this one.

## 2. Install

```bash
apt install -y python3 python3-venv git
su - stanradar
```

As the `stanradar` user:

```bash
git clone <your-repo-url> ~/stanradar
cd ~/stanradar
python3 -m venv .venv
.venv/bin/pip install -e .
```

## 3. Configure

```bash
cp config.example.yaml config.yaml
cp .env.example .env
chmod 600 .env          # the webhook is a credential; don't leave it world-readable
nano .env               # DISCORD_WEBHOOK_URL=... and optionally ANTHROPIC_API_KEY=...
```

Check it works before making it a service — this is the step that catches a typo in the
webhook or an over-tight filter, and it costs nothing:

```bash
.venv/bin/stanradar test-discord     # a message should appear in your channel
.venv/bin/stanradar probe            # see how your filters score what is listed right now
```

If `probe` shows everything rejected, read the reasons in brackets before going further.

## 4. Run it as a service

```bash
mkdir -p ~/.config/systemd/user
cp deploy/stanradar.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now stanradar
```

One thing is easy to miss: a **user** service stops when the user logs out unless lingering
is enabled. Without this, it dies the moment you close SSH.

```bash
exit                                    # back to root
loginctl enable-linger stanradar
```

Verify:

```bash
su - stanradar -c 'systemctl --user status stanradar'
su - stanradar -c 'journalctl --user -u stanradar -f'
```

You should see a priming message per portal in Discord within a minute, then
`cycle: N fetched, 0 new, ...` once a minute in the log.

## 5. Day-to-day

```bash
journalctl --user -u stanradar -f            # live log
journalctl --user -u stanradar --since today # today's cycles
systemctl --user restart stanradar           # after editing config.yaml
stanradar recent -n 40                       # what was seen, scored and alerted
```

**Config changes need a restart** — `config.yaml` is read once at startup.

To update after changing the code:

```bash
cd ~/stanradar && git pull && .venv/bin/pip install -e . && systemctl --user restart stanradar
```

## What can go wrong

| Symptom | Cause | Fix |
|---|---|---|
| Service dies when you log out | Lingering not enabled | `loginctl enable-linger stanradar` as root |
| No alerts, log says "no Discord webhook" | `.env` missing or in the wrong directory | It must sit in `WorkingDirectory` (`~/stanradar/.env`); the app reads it with dotenv, so no quotes needed around the URL |
| `Failed to set up mount namespacing` | A `ReadWritePaths` directory does not exist | Every path listed must exist before the unit starts; the shipped unit points at the repo directory for this reason |
| No alerts, cycles look healthy | Filters too tight | `stanradar probe` and read the rejection reasons |
| One portal fails every cycle | Markup or WAF change | You get one Discord warning after 5 consecutive failures; the other four keep running |
| `ZoneInfoNotFoundError` | Missing tz database | `apt install tzdata` (Linux usually has it; Windows does not) |

Silence is the expected state most of the time. At a €450 ceiling there are roughly 146
qualifying listings in all of Belgrade at any moment, so a handful of alerts a month is the
tool working correctly, not a fault. If you want to confirm it is alive, `stanradar recent`
shows what it has been rejecting and why.

## A note on backups

There is nothing here worth backing up. `data/stanradar.db` holds only which listings have
already been seen — losing it means one noisy re-priming run, not lost data. `config.yaml`
and `.env` are the only files worth keeping a copy of, and `.env` is two lines.
