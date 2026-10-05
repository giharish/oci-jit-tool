terraform {
  required_providers {
    oci = {
      source  = "oracle/oci"
      version = ">= 6.0.0"
    }
  }
}

resource "oci_apigateway_gateway" "jit" {
  compartment_id = var.compartment_ocid
  display_name   = var.gateway_display_name
  endpoint_type  = "PUBLIC"
  subnet_id      = var.subnet_ocid
}

resource "oci_apigateway_deployment" "jit" {
  compartment_id = var.compartment_ocid
  gateway_id     = oci_apigateway_gateway.jit.id
  display_name   = "jit-access-api"
  path_prefix    = "/jit"

  specification {
    logging_policies {
      access_log {
        is_enabled = true
      }
      execution_log {
        is_enabled = true
        log_level  = "INFO"
      }
    }

    routes {
      path    = "/auth"
      methods = ["GET", "POST"]
      backend {
        type        = "ORACLE_FUNCTIONS_BACKEND"
        function_id = lookup(var.function_ocids, "auth", "replace-with-auth-session-fn-ocid")
      }
    }

    routes {
      path    = "/catalog"
      methods = ["GET", "POST"]
      backend {
        type        = "ORACLE_FUNCTIONS_BACKEND"
        function_id = lookup(var.function_ocids, "catalog", "replace-with-catalog-sync-fn-ocid")
      }
    }

    routes {
      path    = "/requests"
      methods = ["GET", "POST"]
      backend {
        type        = "ORACLE_FUNCTIONS_BACKEND"
        function_id = lookup(var.function_ocids, "request", "replace-with-request-access-fn-ocid")
      }
    }

    routes {
      path    = "/approval"
      methods = ["POST"]
      backend {
        type        = "ORACLE_FUNCTIONS_BACKEND"
        function_id = lookup(var.function_ocids, "approval", "replace-with-approval-callback-fn-ocid")
      }
    }

    routes {
      path    = "/policy/preview"
      methods = ["POST"]
      backend {
        type        = "ORACLE_FUNCTIONS_BACKEND"
        function_id = lookup(var.function_ocids, "policy", "replace-with-policy-builder-fn-ocid")
      }
    }

    routes {
      path    = "/revoke"
      methods = ["POST"]
      backend {
        type        = "ORACLE_FUNCTIONS_BACKEND"
        function_id = lookup(var.function_ocids, "revoke", "replace-with-revoke-access-fn-ocid")
      }
    }

    routes {
      path    = "/extension/request"
      methods = ["POST"]
      backend {
        type        = "ORACLE_FUNCTIONS_BACKEND"
        function_id = lookup(var.function_ocids, "extension_request", "replace-with-request-extension-fn-ocid")
      }
    }

    routes {
      path    = "/extension/approve"
      methods = ["POST"]
      backend {
        type        = "ORACLE_FUNCTIONS_BACKEND"
        function_id = lookup(var.function_ocids, "extension_approval", "replace-with-approve-extension-fn-ocid")
      }
    }

    routes {
      path    = "/notifications/warnings"
      methods = ["POST"]
      backend {
        type        = "ORACLE_FUNCTIONS_BACKEND"
        function_id = lookup(var.function_ocids, "notification", "replace-with-notification-worker-fn-ocid")
      }
    }

    routes {
      path    = "/expiry/run"
      methods = ["POST"]
      backend {
        type        = "ORACLE_FUNCTIONS_BACKEND"
        function_id = lookup(var.function_ocids, "expiry", "replace-with-expiry-scheduler-fn-ocid")
      }
    }
  }
}

output "endpoint" {
  value = oci_apigateway_deployment.jit.endpoint
}
