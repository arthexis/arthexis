with Arthexis.ORM;

package Apps.Core.Models.Core_Models is
   """Schema objects owned by the Core Ada app."""

   procedure Install (Conn : in out Arthexis.ORM.Database_Connection);
   --  Create the core tables and indexes.
end Apps.Core.Models.Core_Models;
