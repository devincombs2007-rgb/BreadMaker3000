# Owner setup (iPhone only, about 30 minutes, $0)

Everything runs as **simulation**. You never enter a password or key into chat.

---

## Step 0 - Approve the code (1 min)

1. Open the pull request link from Claude's report.
2. Scroll to the bottom → tap **Merge pull request** → **Confirm merge**.

---

## Step 1 - Tailscale: your private, secure link to the server (5 min)

1. Install **Tailscale** from the App Store: https://apps.apple.com/app/tailscale/id1470499037
2. Open it → **Get Started** → sign in (Apple or Google) → **Allow** the VPN.
3. In Safari open: https://login.tailscale.com/admin/settings/keys
4. Tap **Generate auth key…** → leave every switch **off** → **Generate key** → **Copy**.
   Keep this tab open; you paste it in Step 2.

---

## Step 2 - Create the free Oracle server (10 min tapping, 20 min waiting)

1. Open the setup file: https://github.com/devincombs2007-rgb/BreadMaker3000/blob/main/deploy/cloud-init.yaml
   → tap **Raw** → press and hold → **Select All** → **Copy**.
2. Open https://cloud.oracle.com → sign in.
3. Tap **☰** → **Compute** → **Instances** → **Create instance**.
4. **Name:** `breadmaker`
5. **Image and shape** → **Edit**:
   - **Change image** → **Ubuntu** → **Canonical Ubuntu 24.04** → **Select image**.
   - **Change shape** → **Ampere** → **VM.Standard.A1.Flex** → OCPUs **4**, Memory **24 GB** → **Select shape**.
     (If it says *out of capacity*: try 2 OCPU / 12 GB, or another *Availability domain*, or retry later.)
6. **Add SSH keys** → choose **No SSH keys**.
7. **Boot volume** → turn on **Specify a custom boot volume size** → **100** GB.
8. Tap **Show advanced options** → **Management** → **Paste cloud-init script** → paste.
9. In the pasted text, replace `PASTE-TAILSCALE-AUTH-KEY-HERE` with the key you copied in Step 1.
   - *Only if your GitHub repo is private:* also fill `GH_TOKEN=""` - see the box below.
10. Tap **Create**. Wait about **20 minutes**.

> **Private repo only - read-only GitHub token:** open
> https://github.com/settings/personal-access-tokens/new → name `breadmaker` → Expiration **7 days**
> → Repository access **Only select repositories** → **BreadMaker3000** → Permissions →
> **Contents: Read-only** → **Generate token** → **Copy** → paste between the quotes of `GH_TOKEN=""`.

---

## Step 3 - Open your dashboard (3 min)

1. Open the **Tailscale** app. A device called **breadmaker** appears (green dot).
   Tap it and copy its name (looks like `breadmaker.tail1234.ts.net`).
2. In Safari open `https://` + that name, e.g. `https://breadmaker.tail1234.ts.net`
3. Choose a dashboard password (12+ characters) → let iPhone save it → **Save and continue**.
4. Tap **Share** → **Add to Home Screen** for one-tap access.

The crypto bot starts by itself.

---

## Step 4 - Turn on the stock engine (3 min)

1. Open https://app.alpaca.markets → sign in → make sure the top-left says **Paper**.
2. On the right, under **API Keys**, tap **Generate New Keys** → copy the **Key** and **Secret**.
3. In your dashboard, scroll to **Settings → Alpaca paper keys** → paste both → enter your
   dashboard password → **Save keys**.

The key must start with **PK** (paper). Live keys are refused automatically.

---

## Daily use

- Open the dashboard. Everything is labelled **SIMULATED**.
- Something looks wrong? Tap **Emergency stop all engines**. New trades stop everywhere;
  you need your password to resume.

## Need help?

Tell Claude what you see (no passwords or keys). Claude will give you the next tap-by-tap step.
