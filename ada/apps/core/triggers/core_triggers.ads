with Arthexis.ORM;

package Apps.Core.Triggers.Core_Triggers is
   """SQLite triggers for the Core Ada app."""

   procedure Install (Conn : in out Arthexis.ORM.Database_Connection);
   --  Register core triggers.
end Apps.Core.Triggers.Core_Triggers;
