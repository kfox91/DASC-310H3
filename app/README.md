Author: Kyzer Fox

# Sky Cowboy - Backcountry Aviation Weather

A small aviation weather web app built with Python, Flask, and vanilla HTML/CSS/JS. Enter one or more airport ICAO codes and it pulls live METAR observations from [aviationweather.gov](https://aviationweather.gov), decodes them, works out the FAA flight category, and reads you the briefing in a western drawl.

It runs on **Azure App Service**, with the infrastructure defined in Terraform (`terraform/`) and the app deployed as a zip with the Azure CLI. Deployment follows the class guide: [Azure for Students: Deploying with the CLI](https://github.com/shellywaltersit/platform/blob/main/docs/azure-for-students-cli-deploy.md).

---

## Is it running? Just open it

The dev site, if it's up:

**https://app-webapp-dev-gqu7s.azurewebsites.net**

The `gqu7s` part is a random suffix Terraform picks when it creates the web app, so it changes if the environment is torn down and rebuilt. To find the current URL from any machine, sign in and ask Azure:

```bash
az login
az account set --subscription "Azure for Students"
az webapp list --query "[].{name:name, state:state, url:defaultHostName}" --output table
```

Put `https://` in front of the `url` column. If nothing is listed, nothing is deployed; see [Deploy from scratch](#deploy-from-scratch).

On the free F1 plan the app goes to sleep when idle, so the first request can take 30+ seconds.

---

## Repository layout

```text
DASC-310H3/
├── app/                      # The web app (this is what gets zipped and deployed)
│   ├── app.py                # Flask routes: /, /api/metar, /api/taf
│   ├── weather.py            # Fetching, cleaning, flight category, briefing text, 5-minute cache
│   ├── requirements.txt      # flask, gunicorn, requests, pytest
│   ├── static/               # index.html, app.js, style.css
│   └── tests/                # pytest suite + saved sample METAR/TAF JSON
├── app_test/                 # The guide's Node.js sample app (not deployed)
└── terraform/                # Azure infrastructure
    ├── main.tf               # Resource group, App Service plan, web app, storage, monitoring
    ├── variables.tf
    ├── outputs.tf            # resource_group_name, web_app_name, web_app_url, ...
    ├── providers.tf
    └── environments/
        ├── dev.tfvars        # F1 (free) plan
        └── prod.tfvars       # B1 (paid) plan
```

### API

- `GET /` serves the single-page front end.
- `GET /api/metar?ids=KLXV,KJAC` returns cleaned METAR data for one or more airports.
- `GET /api/taf?ids=KLXV` returns cleaned TAF forecasts.
- Both return `400` if `ids` is missing and `502` if aviationweather.gov can't be reached.

### What `weather.py` does

- Converts units: °C to °F, hPa to inHg, wind degrees to compass points, knots to MPH, and visibility strings like `1 1/2` or `10+` to numbers.
- Fills in the FAA flight category (VFR / MVFR / IFR / LIFR) from ceiling and visibility when the source doesn't provide it.
- Flags reports older than 75 minutes as stale.
- Caches responses in memory for 5 minutes to stay under aviationweather.gov's rate limits.

---

## Run it locally

Requires Python 3.10+.

```bash
cd app
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

pytest -v tests/                          # run the tests
gunicorn --bind 0.0.0.0:8080 app:app      # or: python3 app.py
```

Then open http://localhost:8080.

---

## How it runs on Azure

The web app in `terraform/main.tf` is configured for Python. These three settings matter; without them the deploy "succeeds" but the site doesn't work:

```hcl
site_config {
  application_stack {
    python_version = "3.12"     # Python runtime, not Node
  }
  app_command_line = "gunicorn --bind=0.0.0.0 --timeout 600 app:app"
}

app_settings = {
  SCM_DO_BUILD_DURING_DEPLOYMENT = "true"   # install requirements.txt during zip deploy
}
```

- **`python_version`**: with a Node runtime, Azure ignores `app.py` and shows its default welcome page.
- **`app_command_line`**: starts gunicorn (a production web server) and loads the Flask object `app` from `app.py`.
- **`SCM_DO_BUILD_DURING_DEPLOYMENT`**: without it, Azure only copies files and the app crashes with `No module named 'requests'`.

---

## Prerequisites (any machine)

1. Install the **Azure CLI** (`az`) and **Terraform** (see Part 1 of the guide).
2. Sign in and select the subscription:

   ```bash
   az login
   az account set --subscription "Azure for Students"
   export ARM_SUBSCRIPTION_ID=$(az account show --query id --output tsv)
   ```

   `ARM_SUBSCRIPTION_ID` only lasts for the current terminal, so set it again in each new one.

3. Get the latest code: `git pull`.

All `terraform` commands below run from the `terraform/` folder.

---

## Deploy from scratch

Use this when no `rg-webapp-dev` resource group exists yet (check with `az group list --output table`).

```bash
cd terraform
terraform init
terraform workspace select -or-create dev
terraform workspace show                     # must print: dev

terraform plan -var-file="environments/dev.tfvars" -out="dev.tfplan"
terraform apply "dev.tfplan"
```

Then [deploy the app code](#deploy-or-update-the-app-code).

---

## Deploy or update the app code

### 1. Build the zip

The files must be at the **top level** of the zip, not inside an `app/` folder. Don't use Finder's "Compress", which adds a `__MACOSX` folder.

```bash
# from the repo root
rm -f app.zip
cd app && zip -r ../app.zip . -x '*__pycache__*' '.pytest_cache/*' '.venv/*' '*.DS_Store' && cd ..
unzip -l app.zip            # should list app.py and requirements.txt with no folder in front
```

### 2. Upload it

If this machine has the Terraform state (it ran `apply`, or you followed [Deploying from a different machine](#deploying-from-a-different-machine)):

```bash
cd terraform
az webapp deploy --resource-group "$(terraform output -raw resource_group_name)" \
  --name "$(terraform output -raw web_app_name)" --src-path ../app.zip --type zip
```

If this machine **doesn't** have the Terraform state, you can still deploy code by looking the names up in Azure instead:

```bash
az webapp deploy --resource-group rg-webapp-dev \
  --name "$(az webapp list --resource-group rg-webapp-dev --query '[0].name' --output tsv)" \
  --src-path app.zip --type zip
```

Don't add `--clean true`. It skips the dependency install, and the app crashes with `No module named 'requests'`.

### 3. Check it

Open the site URL and try `/api/metar?ids=KLXV`. The first start after a deploy can take a minute or two on F1.

`az webapp deploy` sometimes reports `Site failed to start within 10 mins` even though the site is running. Check the URL before troubleshooting.

---

## Deploying from a different machine

Terraform tracks what it built in a **state file** stored only on the machine that ran `apply` (`terraform/terraform.tfstate.d/`, which is not committed). A fresh clone on another machine has no state, so Terraform doesn't know the Azure resources exist. A plain `terraform apply` there tries to create everything again and fails because `rg-webapp-dev` already exists.

Choose one of these:

### Option A: You only changed app code (`app/`)

You don't need Terraform. `git pull`, then follow [Deploy or update the app code](#deploy-or-update-the-app-code) using the "doesn't have the Terraform state" command.

### Option B: You need to change infrastructure (`terraform/`)

Adopt the existing resources into this machine's state with `terraform import`. This only writes local state and doesn't change anything in Azure.

```bash
cd terraform
terraform init
terraform workspace select -or-create dev

RG=rg-webapp-dev
APP=$(az webapp list --resource-group $RG --query '[0].name' --output tsv)                # e.g. app-webapp-dev-gqu7s
ST=$(az storage account list --resource-group $RG --query '[0].name' --output tsv)       # e.g. stwebappdevgqu7s
SUFFIX=${APP##*-}                                                                         # e.g. gqu7s
S=/subscriptions/$ARM_SUBSCRIPTION_ID/resourceGroups/$RG
V='-var-file=environments/dev.tfvars'

terraform import $V random_string.suffix "$SUFFIX"
terraform import $V azurerm_resource_group.rg "$S"
terraform import $V azurerm_log_analytics_workspace.law "$S/providers/Microsoft.OperationalInsights/workspaces/law-webapp-dev"
terraform import $V azurerm_application_insights.appi "$S/providers/Microsoft.Insights/components/appi-webapp-dev"
terraform import $V azurerm_service_plan.asp "$S/providers/Microsoft.Web/serverFarms/asp-webapp-dev"
terraform import $V azurerm_storage_account.st "$S/providers/Microsoft.Storage/storageAccounts/$ST"
terraform import $V azurerm_linux_web_app.app "$S/providers/Microsoft.Web/sites/$APP"
```

**Fix the imported suffix before you plan.** Importing `random_string` records `upper = true` and `special = true`, but the code says `false`. Left alone, Terraform will **destroy and recreate the web app and storage account** under new names. Correct it in the local state:

```bash
terraform state pull > state.json
python3 - <<'EOF'
import json
s = json.load(open("state.json"))
for r in s["resources"]:
    if r["type"] == "random_string" and r["name"] == "suffix":
        r["instances"][0]["attributes"].update(upper=False, special=False)
s["serial"] += 1
json.dump(s, open("state.json", "w"), indent=2)
EOF
terraform state push state.json && rm state.json
```

Now plan and **read the summary line before applying**:

```bash
terraform plan -var-file="environments/dev.tfvars" -out="dev.tfplan"
```

It should say `0 to add, ... 0 to destroy`. If anything says `must be replaced` or `to destroy`, stop and find out why. Then `terraform apply "dev.tfplan"`.

### Option C: Start over

Delete the environment and rebuild it from this machine. This gives the app a **new URL**.

```bash
az group delete --name rg-webapp-dev --yes
```

Then follow [Deploy from scratch](#deploy-from-scratch). Delete `terraform/terraform.tfstate.d/dev/` on any other machine that had state, because it now points at resources that no longer exist.

### Keep one machine in charge

Once two machines both have state, either one can change the resources. **Always `git pull` before `terraform plan`/`apply`.** A machine with old code will "fix" the web app back to whatever its code says. For example, a pre-fix checkout switches it back to Node and breaks the site.

---

## Prod

Prod uses the paid **B1** plan, which bills against your Azure credit every hour it exists. It isn't currently deployed. To deploy it, follow Part 7 of the guide (`terraform workspace select -or-create prod`, plan and apply with `environments/prod.tfvars`, then the same zip deploy), and tear it down when you're done.

---

## Tear down

```bash
cd terraform
terraform workspace select dev
terraform destroy -var-file="environments/dev.tfvars"
```

If this machine doesn't have the state: `az group delete --name rg-webapp-dev --yes`. Confirm with `az group list --output table`.

---

## Troubleshooting

| What you see | Cause | Fix |
|---|---|---|
| Azure's default "Microsoft Azure App Service - Welcome" page | Web app is on the Node runtime | `git pull`, then `terraform plan`/`apply` so `python_version = "3.12"` is applied |
| `503` and `No module named 'requests'` in the logs | `requirements.txt` wasn't installed | Make sure `SCM_DO_BUILD_DURING_DEPLOYMENT = "true"` is set and redeploy **without** `--clean true` |
| `Application Error`, or files inside an `app/` folder on the server | Zip has a folder at the top level | Rebuild the zip from inside `app/` as shown above |
| `resource group already exists` on apply | This machine has no state for existing resources | See [Deploying from a different machine](#deploying-from-a-different-machine) |
| Plan wants to replace the web app after an import | Imported `random_string` settings mismatch | Run the state fix in Option B |
| `Site failed to start within 10 mins` from `az webapp deploy` | Often a false alarm on F1 | Open the URL; if it loads, you're done |
| `RequestDisallowedByAzure` | Region not allowed for your subscription | Use a region from your allowed list (currently `northcentralus`) |

To read the app's startup logs:

```bash
az webapp log tail --resource-group rg-webapp-dev --name <web app name>
```
