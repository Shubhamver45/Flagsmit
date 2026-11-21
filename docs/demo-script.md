# Guardian 🛡️ — 3-Minute Video Demo Script
> **Hackathon Track 03: Build on Open Source Flagsmith**  
> *Target Video Duration: 3 minutes (180 seconds)*

---

## Pre-Recording Screen Setup

Open the following browser tabs (or split-screen layout):
* **Left Screen**: [http://localhost:8080/](http://localhost:8080/) — **Guardian Live Status Dashboard**
* **Top Right**: [http://localhost:8000/](http://localhost:8000/) — **Flagsmith Admin UI** (showing `new_checkout` feature state in Development environment)
* **Bottom Right**: Terminal running `./scripts/demo.sh`

---

## Video Script & Storyboard

### Act 1: The Problem (0:00 – 0:45)
**Visual**: Presenter on camera or split view of code shipping rapidly + Guardian dashboard.

> *"With modern AI coding agents, engineering teams are writing and shipping code faster than ever before. But rapid deployment creates an operational nightmare: when a newly shipped feature flag contains a hidden bug—like a payment gateway failure or a memory leak—how quickly can you contain the blast radius?*  
> 
> *Commercial platforms like LaunchDarkly and Datadog charge thousands of dollars for automated guardrails and auto-rollbacks behind steep paywalls. If you are an open-source team self-hosting Flagsmith, you’re often left manually babysitting dashboards, waiting for customer bug reports before an on-call engineer wakes up to flip the flag back to 0%.*  
> 
> *Meet **Guardian**: an open-source service built directly on top of self-hosted Flagsmith that automates phased, guarded rollouts, continuously evaluates operational metrics, executes instant rollbacks on breaches, and alerts your team."*

---

### Act 2: Healthy Rollout Advancing & Human Approval (0:45 – 1:35)
**Visual**: Screen showing Guardian Dashboard (`:8080`) side-by-side with Flagsmith (`:8000`).

> *"Here is Guardian in action with our demo e-commerce application gated by the Flagsmith feature flag `new_checkout`.*  
> 
> *Guardian is configured with a staged rollout pipeline: 1%, 5%, 25%, and 100%, with a soak duration and automated guardrails.*  
> 
> *In the background, our simulator is sending live traffic across hundreds of distinct user identities. As you can see on the dashboard, the error rate is well below our 5% threshold.*  
> 
> *Guardian advances from 1% to 5%, and then to 25%. In Flagsmith, you can see the multivariate allocation updating live through the Admin API.*  
> 
> *Notice what happens before 100%: Guardian enters **AWAITING_APPROVAL**. Rather than blindly shipping to everyone, Guardian holds safely and asks for human sign-off.*  
> 
> *An operator can approve directly with one click on the dashboard, or with a single CLI command:*  
> ```bash
> python3 -m guardian.cli approve --by "release-lead" --reason "Metrics healthy"
> ```  
> *The rollout advances to 100%—fully promoted!"*

---

### Act 3: Fault Injection, Auto-Rollback & Slack Alert (1:35 – 2:25)
**Visual**: Trigger bug injection, watch Guardian immediately detect and rollback.

> *"Now, let's see what happens during a real incident.*  
> 
> *Let's reset the rollout and inject a 30% failure rate into the new checkout flow:*  
> ```bash
> curl -X POST "http://localhost:5001/toggle-bug?enabled=1"
> ```  
> *Immediately, users on the new flow start encountering payment errors. Prometheus scrapes the error count.*  
> 
> *Within seconds, Guardian's Prometheus guardrail evaluates the error rate: it has spiked to **28.9%**, well above our 5.0% threshold.*  
> 
> *Without any human intervention:*  
> 1. *Guardian transitions state to **ROLLED_BACK**.*  
> 2. *It calls the Flagsmith Admin API to instantly set the flag allocation back to **0.0%** and disable the flag.*  
> 3. *Looking at the Flagsmith UI, all user traffic is immediately redirected back to the legacy checkout flow—blast radius eliminated in milliseconds!*  
> 4. *Simultaneously, Guardian dispatches a rich Slack BlockKit alert with the exact breach percentage, sample size, and root-cause evidence."*

---

### Act 4: Where Flagsmith Fits (2:25 – 3:00)
**Visual**: Show Flagsmith UI, feature state details, and `config.yaml`.

> *"Flagsmith is the core feature flag engine that powers Guardian:*  
> * First, Guardian drives Flagsmith's **Master Admin API** for programmatic multivariate percentage allocation and instant 0.0% rollback toggles.*  
> * Second, the demo application evaluates user identities using the official **Flagsmith Client SDK**.*  
> * Third, Guardian respects Flagsmith's internal architecture, cleanly decoupling operational rollout flags from experiment segment overrides to avoid system locks.*  
> * And behind the scenes, Guardian also includes an experiment results evaluator capable of monitoring Flagsmith's Bayesian metrics (`chance_to_win`) and Sample Ratio Mismatch (SRM) alerts.*  
> 
> *Best of all, everything runs locally with one command: `./scripts/demo.sh`.*  
> 
> *Guardian brings enterprise-grade guarded rollouts to open-source Flagsmith—keeping feature delivery fast, automated, and safe. Thank you!"*

---

## Step-by-Step Presenter Cheat-Sheet

| Time | Action | Command / Click | Expected Visual |
| :--- | :--- | :--- | :--- |
| **0:40** | Start Demo Stack | `./scripts/demo.sh` | Status page loads at `:8080`, simulator active |
| **1:00** | Observe Rollout Advancing | Watch Status Page | Stage 1 (1%) ➔ Stage 2 (5%) ➔ Stage 3 (25%) |
| **1:20** | Approve Promotion | Click `[Approve 100%]` in UI or run `python3 -m guardian.cli approve` | State changes to `PROMOTED (100%)` |
| **1:35** | Reset for Incident Demo | Click `[🔄 Reset Rollout to 0%]` | State resets to `PENDING (0%)` |
| **1:45** | Inject Checkout Failure | `curl -s -X POST "http://localhost:5001/toggle-bug?enabled=1"` | Demo app logs errors on `new_flow` |
| **1:55** | Observe Instant Rollback | Watch Status Page & Flagsmith UI | Status changes to `ROLLED_BACK (0%)`, red breach banner |
| **2:15** | Show Alert & Audit Trail | Inspect Timeline Table on Status Page | Full evidence JSON, timestamp, 28.9% error rate |
