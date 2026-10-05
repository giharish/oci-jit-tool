variable "compartment_ocid" {
  description = "Compartment OCID for API Gateway and Functions resources."
  type        = string
}

variable "subnet_ocid" {
  description = "Subnet OCID for the Functions application and API Gateway."
  type        = string
}

variable "gateway_display_name" {
  description = "API Gateway display name."
  type        = string
  default     = "jit-access-gateway"
}

variable "function_ocids" {
  description = "Map of route key to function OCID after functions are deployed. Keys: catalog, request, approval, policy, revoke, extension_request, extension_approval, notification, expiry."
  type        = map(string)
  default     = {}
}
