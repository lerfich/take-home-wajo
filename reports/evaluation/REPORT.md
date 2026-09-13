# G1 measured synthetic evaluation

Generated from saved per-email results; this report command makes no Groq call.

## Result

All 111/111 planned synthetic emails have usable model responses. There are 0 unresolved technical failures. The result is not perfect: the final policy level matched the frozen expectation in 50/72 (69.4%) decision cases.

Safety held in this set: 0/72 decision cases caused an unsafe autonomous primary action. All 6 authored instruction-injection cases were blocked. The main weaknesses were distinguishing Notify and Ask and choosing Archive versus Keep.

## Coverage

Planned: 111 (72 decision, 15 training, 24 control).
Usable: 111; unresolved technical failures: 0; initial provider failures without a model response: 51 (excluded from quality scores).
Continuation result files: 51.

We chose a broader 111-email set to provide more substantial evidence than a small smoke test. The original free Groq account reached its daily allowance after 60 usable responses. The remaining frozen IDs were completed on the same primary `qwen/qwen3.8-27b` model with a new free-account credential. Only usable responses from that primary model contribute to quality scores.

## Decision quality

### `qwen/qwen3.8-27b`

Usable saved responses: 111 (decision 72, training 15, control 24). Recorded successful-call tokens: 321047.

| Check | Correct |
| --- | ---: |
| Final policy level | 50/72 (69.4%) |
| Reply/action classification | 63/72 (87.5%) |
| Archive/Keep recommendation | 46/72 (63.9%) |
| Semantic label kind | 59/72 (81.9%) |
| Attention cue | 63/72 (87.5%) |
| Event kind | 64/72 (88.9%) |
| Suspicion classification | 71/72 (98.6%) |
| Preference transfer | 24/24 (100.0%) |

### Four-level comparison

Blocked is shown separately for hard safety stops such as instruction injection and unsupported operations.

| Frozen expected level | Correct | Observed final outcomes |
| --- | ---: | --- |
| Silent | 18/18 (100.0%) | silent 18 |
| Notify | 6/18 (33.3%) | escalate 4, notify 6, silent 8 |
| Ask | 9/18 (50.0%) | ask 9, escalate 3, notify 1, silent 5 |
| Escalate | 17/18 (94.4%) | blocked 12, escalate 6 |

Unsafe autonomous primary actions: 0/72.
Attack detection: 6/6.

### Main error patterns

One email can contribute to more than one component mismatch.

| Pattern | Count |
| --- | ---: |
| Expected Keep, model suggested Archive | 26 |
| Expected reply, model proposed no action | 9 |
| Expected event/deadline, model extracted none | 7 |
| Expected no event, model extracted one | 1 |
| Benign case marked suspicious | 1 |
| Authored injection missed | 0 |


## Learning and memory

Scripted evaluation-user feedback was saved for 15/15 training emails. The same model proposal for each control was evaluated before and after applying Wajo's stored preference; Qwen was not retrained.

| Preference scenario | Training emails | Similar controls | Contrasting controls | Total transfer |
| --- | ---: | ---: | ---: | ---: |
| Application receipt / Organization | 3 | 3/3 | 2/2 | 5/5 |
| Support receipt / Organization | 3 | 3/3 | 2/2 | 5/5 |
| Newsletter / Organization | 3 | 3/3 | 2/2 | 5/5 |
| Account security / Attention | 3 | 3/3 | 2/2 | 5/5 |
| Schedule change / Attention | 3 | 2/2 | 2/2 | 4/4 |
| **All** | **15** | **14/14** | **10/10** | **24/24** |

These controls test narrowly scoped Organization and Attention preferences. Those preference families do not change send authority or the four-level autonomy decision, so this run does not measure a reduction in Ask prompts.

### Expected improvement with continued feedback

As the user confirms, rejects and corrects proposals, Wajo can accumulate narrowly scoped Skills for recurring situations. We expect this to increase the share of correct final outcomes on genuinely similar, eligible future emails and to reduce repeated review for the specific actions that a Skill is allowed to perform. Negative feedback can narrow, reset or revoke a learned preference.

This is a product hypothesis supported here only by 24/24 Organization/Attention transfer controls; it is not a measured forecast for overall accuracy. Feedback changes Wajo's stored policy state, not Qwen's weights, and it does not fix the model's interpretation of novel situations. Confirming a proposal also never expands payment, deletion or ordinary send authority.

The current error profile suggests three separate model improvements: bias Archive toward Keep when uncertainty or significance exists, clarify the Notify/Ask boundary and strengthen event/deadline extraction. Any prompt or policy change must receive a new version and be measured on a fresh frozen set rather than rescoring these observed cases.

## Reproducibility

| Frozen item | Value |
| --- | --- |
| Model | `qwen/qwen3.8-27b` |
| Prompt version | `email-analysis-prompt-v9` |
| Dataset SHA-256 | `83c6a7f67f3e58356af3e5eea93a918df90d778f0424f2a03244f3e6d9ae27b0` |
| Prompt SHA-256 | `30565703404a93af5f84587d80ab1750a80792fc63039c04adf68561783625e9` |
| Policy/core SHA-256 | `cfcdb9e39024674c9f9cef647108469a4854ff60fa50e5907d438971653be0f4` |
| Initial harness SHA-256 | `4321088adaa8d899011d8fe626bde5dfc73ad013e25a14707a44e05e7b48521e` |
| Continuation harness SHA-256 | `030293bc4d7f274b17efdefbc70b3ce5d02ab14358121e1101a0d89e4ad4fb1c` |

## Attempt history

The initial `qwen/qwen3.8-27b` run reached the original free Groq account's daily token allowance after 60 usable responses. The remaining IDs were repeated in a second attempt using a new free-account credential and the same model, prompt, adapter, policy and frozen expectations. Provider failures without a model response are infrastructure events and do not enter quality scores.

All completed model responses counted by this report use the same primary model. Incorrect classifications remain in the scores; technical failures without a response may be retried and replaced.

## Limits

All emails are synthetic. Scripted feedback is from an evaluation user, not Nikita. Qwen weights did not change; preferences are stored in Wajo state.
This direct adapter/policy test has no Gmail binding, transport delivery, UI, external sends or private inbox. Archive and Event Skill qualification that requires verified Gmail messages is outside this run.
Before/after controls reuse one actual model response and isolate preference memory. Prior functional checks in [VERIFICATION.md](../../VERIFICATION.md) are not included in these denominators.
