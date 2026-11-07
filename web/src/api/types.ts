/** Friendly names for the API's generated OpenAPI types (`schema.d.ts`, `npm run gen:api`). */
import type { components } from "./schema";

type S = components["schemas"];

export type Domain = S["DomainInfo"]["id"];
export type DomainInfo = S["DomainInfo"];
export type Health = S["Health"];
export type Product = S["Product-Output"];
export type ProductHit = S["ProductHit"];
export type Cocktail = S["Cocktail"];
export type CocktailHit = S["CocktailHit"];
export type Book = S["Book"];
export type BookHit = S["BookHit"];
export type BookPairing = S["BookPairing"];
export type Pairing = S["Pairing"];
export type PairingRequest = S["PairingRequest"];
export type DemoCustomer = S["DemoCustomer"];
export type Outfit = S["Outfit"];
export type OutfitSlot = S["OutfitSlot"];
export type OutfitState = S["OutfitState"];
export type EveningPlan = S["EveningPlan"];
export type EveningState = S["EveningState"];
export type EveningRequest = S["EveningRequest"];
export type EveningSwap = S["EveningSwap"];
export type EveningTastes = S["EveningTastes"];
export type AgentStep = S["AgentStep"];
export type AssistantReply = S["AssistantReply"];
export type PendingAction = S["PendingAction-Output"];
export type ApprovalTool = PendingAction["tool"];
export type GroundingReport = S["GroundingReport"];
export type Clarification = S["Clarification"];
export type AppliedEffect = NonNullable<AssistantReply["applied"]>[number];
export type ChatTurn = S["ChatTurn"];
export type ChatRequest = S["ChatRequest"];
export type ChatFeedback = S["ChatFeedback"];
export type ShopperPreferences = S["ShopperPreferences"];
export type CocktailPreferences = S["CocktailPreferences"];
export type BookPreferences = S["BookPreferences"];

/** `/chat/stream` lines: a step per agent step, then the reply (or an error). */
export type StreamEvent =
  | { type: "step"; step: AgentStep }
  | { type: "reply"; reply: AssistantReply }
  | { type: "error"; status?: number; detail?: string };
