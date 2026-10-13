# MetroOffice Ride Operations

## Admin dashboard setup

The operations dashboard is served by the API at `/admin` and its API endpoints require HTTP Basic authentication.

1. In Render, open the `metrooffice-api` web service and add `ADMIN_USERNAME` and `ADMIN_PASSWORD` as environment variables. `render.yaml` declares these as manual secret values.
2. Use a dedicated username and a long, unique password (preferably generated and stored in a password manager). Do not put credentials in Git, the static website, or WhatsApp.
3. Redeploy/restart the API service after setting them.
4. Open `https://<your-api-host>/admin` and enter those credentials when prompted. Use the HTTPS URL only.

If the credentials are not set, admin endpoints return `503` and the dashboard remains unavailable.

## Daily operations

- **Prepare departures:** Choose the date and click **Generate schedule**. This creates the departures configured in `config/routes.yaml`; it does not change existing saved rides.
- **Edit a ride:** Select the departure, change its date/time, fare, or driver details, then save. A fare change applies to future bookings; seats already booked retain their original amount. Rescheduling queues a WhatsApp notice for booked passengers.
- **Cancel a ride:** Select **Cancel ride** and confirm. The ride stops accepting bookings and cancellation notices are queued for booked passengers. This does not issue refunds or rebook anyone; handle those with the customer/payment provider.
- **Passenger manifest:** Select a ride to see booked seats, passenger name/phone, and payment status. Treat this personal information as confidential and share it only with staff assigned to that ride.
- **Cash reconciliation:** The dashboard shows cash due, cash collected, and online paid totals. After the driver receives a cash fare, use **Cash received** for that seat. This action is recorded and is safe to repeat.

WhatsApp outbound reschedule/cancellation messages may fail outside the customer-service window unless an approved template is implemented. A queued notification count is not a delivery receipt. Check provider status and contact customers through an approved channel when necessary.

Older bookings created before payment status was recorded show `unknown`; verify those manually rather than assuming their payment method.

## Production-readiness limits

The current booking store writes JSON files under `data/seat_inventory`, and pending checkout/conversation state is partly held in process memory. These changes make operations manageable for a controlled pilot, but JSON files and in-memory payment state are not a robust multi-instance production database. Before scaling or relying on it for live paid rides, migrate service, booking, payment, and idempotency records to PostgreSQL (or another durable transactional store), deploy backups, and verify the Render service's persistent-storage configuration. Test a restore and a deployment/restart before accepting real volume.

Also complete the WhatsApp production checklist: verify webhook signatures, secure/remove diagnostic endpoints as appropriate, use approved templates for out-of-window messages, and test payment-provider production callbacks and refund handling.
