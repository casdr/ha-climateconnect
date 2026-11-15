# Itho Daalderop ClimateConnect for Home Assistant

A custom integration for Home Assistant that connects Itho Daalderop boilers with
ClimateConnect wifi, such as the Smart-upp boiler, through the same cloud services
as the ClimateConnect app.

This integration is not made or supported by Itho Daalderop.

## Features

Per boiler:

| Entity | What it does |
| --- | --- |
| **Mode** (select) | Smart control, Schedule, Continuous or Holiday. Switching keeps the boiler's setpoint and weekly schedule. |
| **Setpoint** (number) | Water temperature in Continuous mode. Setting it selects Continuous mode. |
| **Boost** (switch) | Heat the boiler now. |
| **Vacation mode** (switch) | Turns vacation mode on or off for the period set below. |
| **Vacation start / end** (date-time) | The vacation period. A start after the current end moves the end a week later. |
| **Energy level** (sensor) | The boiler's energy level in percent, as the app shows it. |
| **Power** (sensor) | Power the boiler uses now. |
| **Energy today / yesterday** (sensor) | Energy use per day (kWh), with the day's costs as an attribute. The service's days start at 00:00 UTC. |
| **Next legionella cycle** (sensor) | Hours until the next anti-legionella cycle. |
| **Fault** (binary sensor) | On while the boiler reports a fault, with the code and the app's (Dutch) text. |
| **Last fault** (sensor) | When the last fault started, with the last 8 faults as attributes. |
| **Electricity tariff** (sensor) | The price per kWh ClimateConnect uses to calculate costs. |
| **Connected / Connection state** (diagnostic) | Whether the boiler is online. |

### Energy dashboard

The energy use and costs per day are imported as long-term statistics:

- `climateconnect:<serial>_energy` (kWh)
- `climateconnect:<serial>_costs` (EUR, based on the tariff)

Add the energy statistic under **Settings → Dashboards → Energy → Individual
devices**. The service's days start at 00:00 UTC, so each day shows up at 01:00
(winter) or 02:00 (summer). Today's value grows during the day.

### Actions

- **`climateconnect.set_schedule`**: replaces the weekly schedule and switches the
  boiler to Schedule mode.
- **`climateconnect.get_schedule`**: returns the mode and schedule in the same
  format.
- **`climateconnect.set_tariff`**: sets the electricity tariff for the account, for
  example from your energy contract or a dynamic price sensor. ClimateConnect also
  changes the dates of earlier tariffs, so a start date in the past changes the cost
  history.

```yaml
action: climateconnect.set_schedule
data:
  device_id: 0123456789abcdef0123456789abcdef
  schedule:
    monday:
      - time: "06:00"
        temperature: 65
      - time: "19:00"
        temperature: 75
    saturday:
      - time: "08:00"
        temperature: 70
```

Weekdays you leave out get no switch moments.

```yaml
action: climateconnect.set_tariff
data:
  device_id: 0123456789abcdef0123456789abcdef
  price: 0.25
  start_date: "2027-01-01"  # optional, default today
```

## Installation

### HACS

1. In HACS, open **⋮ → Custom repositories** and add
   `https://github.com/casdr/ha-climateconnect` as an **Integration**.
2. Install **Itho Daalderop ClimateConnect** and restart Home Assistant.

### Manual

Copy `custom_components/climateconnect` into your configuration's
`custom_components` folder and restart Home Assistant.

## Configuration

**Settings → Devices & services → Add integration → Itho Daalderop ClimateConnect**,
then sign in with the e-mail address and password of the ClimateConnect app. All
boilers of the account are added.

Under **Configure** you can set the update interval (default 5 minutes). Vacation
mode, faults and the tariff are updated every 15 minutes, energy history every hour.

The boiler takes a few seconds to report a change. Changed values show right away
and the boiler is read again 30 seconds later.

To change the password, use **Reconfigure**. To use another account, add the
integration again.

The password is stored in Home Assistant to sign in again when needed. The session
token is renewed daily and never written to disk. If ClimateConnect rejects the
password, Home Assistant asks for the new one.

## Troubleshooting

Download the diagnostics (**Settings → Devices & services → Itho Daalderop
ClimateConnect → ⋮ → Download diagnostics**) and attach them to an issue. The e-mail
address, password and serial numbers are redacted.

If the sign-in stops at an unsupported page, for example new terms of use, sign in
once in the ClimateConnect app, then reload the integration.

## Development

See [CONTRIBUTING.md](.github/CONTRIBUTING.md).
