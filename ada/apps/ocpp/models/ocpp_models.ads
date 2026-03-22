with Arthexis.ORM;

package Apps.OCPP.Models.OCPP_Models is
   --  Schema objects owned by the OCPP Ada app.

   procedure Install (Conn : in out Arthexis.ORM.Database_Connection);
   --  Create OCPP data tables and indexes.
end Apps.OCPP.Models.OCPP_Models;
